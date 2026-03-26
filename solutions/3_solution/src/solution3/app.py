from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from solution3.api.admin_routes import register_admin_routes
from solution3.api.auth_routes import register_auth_routes
from solution3.api.error_responses import api_error_response
from solution3.api.task_read_routes import register_task_read_routes
from solution3.api.task_write_routes import register_task_write_routes
from solution3.core.runtime import RuntimeState
from solution3.core.settings import AppSettings, load_settings
from solution3.db.migrate import run_migrations
from solution3.models.schemas import HealthResponse, ReadyResponse
from solution3.utils.logging import configure_logging, get_logger

logger = get_logger("solution3.app")


def _register_api_routes(app: FastAPI) -> None:
    router = APIRouter()
    register_auth_routes(router)
    register_task_write_routes(router)
    register_task_read_routes(router)
    register_admin_routes(router)
    app.include_router(router)


def _register_system_routes(app: FastAPI) -> None:
    @app.get("/health", tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            solution="3_solution",
            timestamp=datetime.now(UTC).isoformat(),
        )

    @app.get("/ready", tags=["system"])
    def ready() -> ReadyResponse:
        return ReadyResponse.with_defaults(
            ready=True,
            deps=["postgres", "redis", "rabbitmq", "hydra", "redpanda", "tigerbeetle"],
        )


async def _build_runtime(*, settings: AppSettings) -> RuntimeState:
    await run_migrations(str(settings.postgres_dsn))
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await redis_client.ping()
    return RuntimeState(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        started=True,
    )


async def _close_runtime(runtime: RuntimeState) -> None:
    if runtime.redis_client is not None:
        await runtime.redis_client.close()
    if runtime.db_pool is not None:
        await runtime.db_pool.close()


def create_app(*, initialize_runtime: bool = False) -> FastAPI:
    settings = load_settings()
    configure_logging(enable_sensitive=getattr(settings, "log_leak_sensitive_values", False))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = getattr(app.state, "runtime", None)
        created_runtime = False
        if runtime is None:
            runtime = (
                await _build_runtime(settings=settings)
                if initialize_runtime
                else RuntimeState(settings=settings, started=True)
            )
            created_runtime = True
        else:
            runtime.started = True
        app.state.runtime = runtime
        logger.info(
            "solution3_runtime_started",
            app_name=settings.app_name,
            app_env=settings.app_env,
            initialize_runtime=initialize_runtime,
        )
        try:
            yield
        finally:
            runtime.started = False
            if created_runtime and initialize_runtime:
                await _close_runtime(runtime)
            logger.info("solution3_runtime_stopped", app_name=settings.app_name)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: object, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0]
        message = str(first.get("msg", "Invalid request"))
        return api_error_response(status_code=400, code="BAD_REQUEST", message=message)

    _register_api_routes(app)
    _register_system_routes(app)
    return app
