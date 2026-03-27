from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import asyncpg
import tigerbeetle as tb
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from solution3.api.admin_routes import register_admin_routes
from solution3.api.auth_routes import register_auth_routes
from solution3.api.error_responses import api_error_response
from solution3.api.paths import METRICS_PATH
from solution3.api.task_read_routes import register_task_read_routes
from solution3.api.task_write_routes import register_task_write_routes
from solution3.core.runtime import RuntimeState
from solution3.core.settings import AppSettings, load_settings
from solution3.db.migrate import run_migrations
from solution3.db.repository import list_active_users_with_initial_credits
from solution3.models.schemas import HealthResponse, ReadyResponse
from solution3.observability.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL
from solution3.services.billing import TigerBeetleBilling, resolve_tigerbeetle_addresses
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

    @app.get(METRICS_PATH, tags=["system"])
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _canonical_path_label(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return request.url.path


async def _build_runtime(*, settings: AppSettings) -> RuntimeState:
    await run_migrations(str(settings.postgres_dsn))
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await redis_client.ping()
    billing_client = await _bootstrap_tigerbeetle(db_pool=db_pool, settings=settings)
    return RuntimeState(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        billing_client=billing_client,
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

    @app.middleware("http")
    async def _metrics_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.perf_counter()
        method = request.method
        try:
            response = await call_next(request)
        except Exception:
            path = _canonical_path_label(request)
            duration = time.perf_counter() - start
            HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status="500").inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(duration)
            raise

        path = _canonical_path_label(request)
        duration = time.perf_counter() - start
        HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=str(response.status_code)).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(duration)
        return response

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


def _build_billing(settings: AppSettings) -> TigerBeetleBilling:
    client = tb.ClientSync(
        cluster_id=settings.tigerbeetle_cluster_id,
        replica_addresses=resolve_tigerbeetle_addresses(settings.tigerbeetle_endpoint),
    )
    return TigerBeetleBilling(
        client=client,
        ledger_id=settings.tigerbeetle_ledger_id,
        revenue_account_id=settings.tigerbeetle_revenue_account_id,
        escrow_account_id=settings.tigerbeetle_escrow_account_id,
        pending_timeout_seconds=settings.tigerbeetle_pending_transfer_timeout_seconds,
    )


async def _bootstrap_tigerbeetle(
    *, db_pool: asyncpg.Pool, settings: AppSettings
) -> TigerBeetleBilling:
    billing_client = _build_billing(settings)
    seed_users = await list_active_users_with_initial_credits(db_pool)
    await asyncio.to_thread(billing_client.ensure_platform_accounts)
    for user_id, initial_credits in seed_users:
        await asyncio.to_thread(
            billing_client.ensure_user_account,
            user_id,
            initial_credits=initial_credits,
        )
    logger.info("solution3_tigerbeetle_bootstrapped", user_count=len(seed_users))
    return billing_client
