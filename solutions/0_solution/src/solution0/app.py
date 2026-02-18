"""FastAPI application assembly and shared runtime helpers for Solution 0."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import cast

import asyncpg
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from solution0.api.admin_routes import register_admin_routes
from solution0.api.contracts import (
    AdminRoutesApp,
    SystemRoutesApp,
    TaskReadRoutesApp,
    TaskWriteRoutesApp,
)
from solution0.api.system_routes import register_system_routes
from solution0.api.task_read_routes import register_task_read_routes
from solution0.api.task_write_routes import register_task_write_routes
from solution0.constants import (
    ADMIN_ROLE,
    DEFAULT_TASK_STATUS,
    TASK_CANCELLABLE_STATUSES,
    TASK_RUNNING_STATUSES,
    TASK_TERMINAL_STATUSES,
    TaskStatus,
)
from solution0.core.dependencies import DependencyHealthService, build_dependency_health_service
from solution0.core.runtime import RuntimeState
from solution0.core.settings import AppSettings, load_settings
from solution0.db.migrate import run_migrations
from solution0.db.repository import (
    admin_update_user_credits,
    create_task_record,
    get_task,
    insert_credit_transaction,
    update_task_cancelled,
    update_task_expired,
    update_task_failed,
)
from solution0.models.domain import AuthUser, TaskRecord
from solution0.models.schemas import (
    AdminCreditsRequest,
    AdminCreditsResponse,
    CancelTaskResponse,
    ErrorEnvelope,
    ErrorPayload,
    PollTaskResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
)
from solution0.observability.metrics import (
    CELERY_QUEUE_DEPTH,
    CREDIT_DEDUCTIONS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    TASK_SUBMISSIONS_TOTAL,
)
from solution0.services.auth import (
    credits_cache_key,
    idempotency_key,
    invalidate_user_auth_cache,
    parse_bearer_token,
    pending_marker_key,
    resolve_user_from_api_key,
    result_cache_key,
)
from solution0.services.billing import (
    hydrate_credits_from_db,
    refund_and_decrement_active,
    run_admission_gate,
)
from solution0.utils.logging import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)
from solution0.utils.lua_scripts import ADMISSION_LUA, DECR_ACTIVE_CLAMP_LUA
from solution0.workers.celery_app import celery_app

logger = get_logger("solution0.api")

# Route modules receive this module object and resolve these symbols dynamically.
# Tests monkeypatch these names directly on `solution0.app`.
_ROUTE_MODULE_EXPORTS: tuple[object, ...] = (
    ADMIN_ROLE,
    DEFAULT_TASK_STATUS,
    TASK_CANCELLABLE_STATUSES,
    TASK_RUNNING_STATUSES,
    TASK_TERMINAL_STATUSES,
    TaskStatus,
    AdminCreditsRequest,
    AdminCreditsResponse,
    CancelTaskResponse,
    PollTaskResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
    CELERY_QUEUE_DEPTH,
    CREDIT_DEDUCTIONS_TOTAL,
    TASK_SUBMISSIONS_TOTAL,
    admin_update_user_credits,
    create_task_record,
    get_task,
    insert_credit_transaction,
    update_task_cancelled,
    update_task_expired,
    update_task_failed,
    credits_cache_key,
    idempotency_key,
    invalidate_user_auth_cache,
    pending_marker_key,
    result_cache_key,
    hydrate_credits_from_db,
    refund_and_decrement_active,
    run_admission_gate,
    celery_app,
)


class _TaskCancellationConflict(Exception):
    """Raised when cancel transition loses a status race."""

    pass


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retry_after: int | None = None,
) -> JSONResponse:
    """Build the canonical JSON error envelope."""
    payload = ErrorEnvelope(error=ErrorPayload(code=code, message=message, retry_after=retry_after))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _runtime_state(request: Request) -> RuntimeState:
    """Fetch initialized runtime resources from app state."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("runtime state is not initialized")
    return cast(RuntimeState, runtime)


def _health_service(request: Request) -> DependencyHealthService:
    """Fetch dependency-health service from app state."""
    service = getattr(request.app.state, "dependency_health", None)
    if service is None:
        raise RuntimeError("dependency health service is not initialized")
    return cast(DependencyHealthService, service)


async def _authenticate(request: Request) -> AuthUser:
    """Resolve bearer token to authenticated user with cache-aside lookup."""
    runtime = _runtime_state(request)
    token = parse_bearer_token(request.headers.get("Authorization"))
    if token is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    try:
        user = await resolve_user_from_api_key(
            api_key=token,
            redis_client=runtime.redis_client,
            db_pool=runtime.db_pool,
            auth_cache_ttl_seconds=runtime.settings.auth_cache_ttl_seconds,
        )
    except Exception as exc:
        logger.exception("auth_resolution_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Authentication backend unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    return user


def _task_expires_at(task: TaskRecord, ttl_seconds: int) -> datetime:
    """Compute result visibility expiry from task completion/creation time."""
    base = task.completed_at or task.created_at
    return base + timedelta(seconds=ttl_seconds)


async def _check_worker_connectivity(timeout_seconds: float = 1.0) -> bool:
    """Probe Celery worker control plane connectivity."""

    def _probe() -> bool:
        inspect = celery_app.control.inspect(timeout=timeout_seconds)
        response = inspect.ping()
        return bool(response)

    try:
        return await asyncio.to_thread(_probe)
    except Exception:
        return False


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and teardown external resources for the API process."""
    configure_logging()
    settings = load_settings()

    await run_migrations(str(settings.postgres_dsn))

    db_pool = await asyncpg.create_pool(
        dsn=str(settings.postgres_dsn),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=settings.db_pool_command_timeout_seconds,
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime_seconds,
        server_settings={
            "statement_timeout": f"{settings.db_statement_timeout_ms}ms",
            "idle_in_transaction_session_timeout": (
                f"{settings.db_idle_in_transaction_timeout_ms}ms"
            ),
        },
    )
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        max_connections=50,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    await redis_client.ping()

    admission_script_sha = cast(str, await redis_client.script_load(ADMISSION_LUA))
    decrement_script_sha = cast(
        str,
        await redis_client.script_load(DECR_ACTIVE_CLAMP_LUA),
    )

    app.state.runtime = RuntimeState(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        admission_script_sha=admission_script_sha,
        decrement_script_sha=decrement_script_sha,
    )
    app.state.dependency_health = build_dependency_health_service(
        settings,
        db_pool=db_pool,
        redis_client=redis_client,
    )

    logger.info("startup_complete", app_name=settings.app_name)

    yield

    await redis_client.close()
    await db_pool.close()
    logger.info("shutdown_complete")


def create_app(
    settings: AppSettings | None = None,
    dependency_health: DependencyHealthService | None = None,
) -> FastAPI:
    """Create the FastAPI application for Solution 0."""

    # Optional injection is preserved for testing, but runtime uses lifespan initialization.
    _ = settings
    _ = dependency_health

    app = FastAPI(title="mc-solution0-api", lifespan=_lifespan)

    @app.middleware("http")
    async def _metrics_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        method = request.method
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        request.state.trace_id = trace_id
        clear_log_context()
        bind_log_context(trace_id=trace_id, path=path, method=method)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status="500").inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(duration)
            raise
        finally:
            clear_log_context()

        duration = time.perf_counter() - start
        HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=str(response.status_code)).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(duration)
        return response

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 401:
            return _error_response(
                status_code=401,
                code="UNAUTHORIZED",
                message="Missing or invalid bearer token",
            )
        if exc.status_code == 503:
            return _error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if exc.status_code == 404:
            return _error_response(status_code=404, code="NOT_FOUND", message="Resource not found")
        if exc.status_code == 409:
            return _error_response(status_code=409, code="CONFLICT", message=str(exc.detail))
        return _error_response(
            status_code=exc.status_code,
            code="HTTP_ERROR",
            message=str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        detail = exc.errors()[0]["msg"] if exc.errors() else "Invalid request"
        return _error_response(status_code=400, code="BAD_REQUEST", message=str(detail))

    app_module = sys.modules[__name__]
    register_system_routes(app, cast(SystemRoutesApp, app_module))
    register_task_read_routes(app, cast(TaskReadRoutesApp, app_module))
    register_task_write_routes(app, cast(TaskWriteRoutesApp, app_module))
    register_admin_routes(app, cast(AdminRoutesApp, app_module))

    return app
