from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from solution3.core.runtime import RuntimeState
from solution3.core.settings import load_settings
from solution3.models.schemas import HealthResponse, ReadyResponse
from solution3.utils.logging import configure_logging, get_logger

logger = get_logger("solution3.app")


def _build_app() -> FastAPI:
    settings = load_settings()
    configure_logging(enable_sensitive=getattr(settings, "log_leak_sensitive_values", False))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = RuntimeState(settings=settings, started=True)
        app.state.runtime = runtime
        logger.info(
            "solution3_runtime_started",
            app_name=settings.app_name,
            app_env=settings.app_env,
        )
        try:
            yield
        finally:
            runtime.started = False
            logger.info("solution3_runtime_stopped", app_name=settings.app_name)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

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

    return app


def create_app() -> FastAPI:
    """Factory used by uvicorn and integration tests."""

    return _build_app()
