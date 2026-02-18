"""Celery worker runtime and task execution flow for Solution 0."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
from celery import Task
from celery.signals import worker_process_init, worker_process_shutdown
from prometheus_client import start_http_server
from redis import Redis
from redis.exceptions import NoScriptError

from solution0.constants import TaskCompletionMetricStatus, TaskStatus
from solution0.core.settings import AppSettings, load_settings
from solution0.db.migrate import run_migrations
from solution0.db.repository import (
    insert_credit_transaction,
    update_task_completed,
    update_task_failed,
    update_task_running,
)
from solution0.observability.metrics import TASK_COMPLETIONS_TOTAL
from solution0.services.auth import active_tasks_key, credits_cache_key, result_cache_key
from solution0.utils.logging import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)
from solution0.utils.lua_scripts import DECR_ACTIVE_CLAMP_LUA
from solution0.utils.retry import retry_async
from solution0.workers.celery_app import celery_app

logger = get_logger("solution0.worker")


DB_POOL_ACQUIRE_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def _acquire_db_connection(
    pool: asyncpg.Pool,
    *,
    timeout_seconds: float = DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
) -> AsyncIterator[asyncpg.Connection]:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with pool.acquire() as connection:
                yield connection
    except TimeoutError as exc:
        raise TimeoutError(
            f"Timed out waiting {timeout_seconds:.1f}s for PostgreSQL connection from pool"
        ) from exc


class WorkerModel:
    """Simulated model worker from assignment baseline."""

    def __init__(self) -> None:
        logger.info("worker_initializing")
        time.sleep(10)
        logger.info("worker_initialized")

    def __call__(self, x: int, y: int) -> int:
        logger.info("worker_processing", x=x, y=y)
        time.sleep(2)
        return x + y


@dataclass
class WorkerRuntime:
    """In-process worker resources initialized once per Celery child process."""

    event_loop: asyncio.AbstractEventLoop
    loop_thread: threading.Thread
    db_pool: asyncpg.Pool
    redis_client: Redis[str]
    decrement_script_sha: str
    model: WorkerModel


@dataclass(frozen=True)
class TaskExecutionContext:
    """Immutable inputs/state needed by one task execution lifecycle."""

    runtime: WorkerRuntime
    settings: AppSettings
    task_id: str
    user_id: str
    task_uuid: UUID
    user_uuid: UUID
    x: int
    y: int
    cost: int
    api_key: str
    retry_attempts: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float


_runtime: WorkerRuntime | None = None
_runtime_lock = threading.Lock()
_metrics_server_started = False


def _settings() -> AppSettings:
    return load_settings()


async def _db_pool() -> asyncpg.Pool:
    settings = _settings()
    return await asyncpg.create_pool(
        dsn=str(settings.postgres_dsn),
        min_size=settings.db_pool_min_size,
        max_size=max(2, min(settings.db_pool_max_size, 8)),
        command_timeout=settings.db_pool_command_timeout_seconds,
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime_seconds,
        server_settings={
            "statement_timeout": f"{settings.db_statement_timeout_ms}ms",
            "idle_in_transaction_session_timeout": (
                f"{settings.db_idle_in_transaction_timeout_ms}ms"
            ),
        },
    )


def _loop_thread_entry(event_loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(event_loop)
    event_loop.run_forever()


def _run_coroutine_on_worker_loop[T](
    event_loop: asyncio.AbstractEventLoop,
    coroutine: Coroutine[Any, Any, T],
    *,
    timeout_seconds: float | None,
) -> T:
    """Run async DB/migration work on the dedicated worker event loop."""
    future = asyncio.run_coroutine_threadsafe(coroutine, event_loop)
    try:
        if timeout_seconds is None:
            return future.result()
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"worker loop coroutine timed out after {timeout_seconds:.2f}s") from exc


def _stop_event_loop(
    event_loop: asyncio.AbstractEventLoop,
    *,
    loop_thread: threading.Thread,
    timeout_seconds: float,
) -> None:
    if event_loop.is_running():
        event_loop.call_soon_threadsafe(event_loop.stop)
    loop_thread.join(timeout=timeout_seconds)
    if loop_thread.is_alive():
        logger.warning(
            "worker_event_loop_join_timeout",
            timeout_seconds=timeout_seconds,
            thread_name=loop_thread.name,
        )
        return
    if not event_loop.is_closed():
        event_loop.close()


def _bootstrap_runtime(settings: AppSettings) -> WorkerRuntime:
    """Initialize migrations, pool, Redis scripts, and simulated model."""
    event_loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(
        target=_loop_thread_entry,
        args=(event_loop,),
        name="solution0-worker-asyncio",
        daemon=True,
    )
    loop_thread.start()

    try:
        _run_coroutine_on_worker_loop(
            event_loop,
            run_migrations(str(settings.postgres_dsn)),
            timeout_seconds=settings.worker_loop_bootstrap_timeout_seconds,
        )
        db_pool = _run_coroutine_on_worker_loop(
            event_loop,
            _db_pool(),
            timeout_seconds=settings.worker_loop_bootstrap_timeout_seconds,
        )
    except Exception:
        _stop_event_loop(
            event_loop,
            loop_thread=loop_thread,
            timeout_seconds=settings.worker_loop_shutdown_timeout_seconds,
        )
        raise

    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        max_connections=50,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    decrement_script_sha = str(redis_client.script_load(DECR_ACTIVE_CLAMP_LUA))
    return WorkerRuntime(
        event_loop=event_loop,
        loop_thread=loop_thread,
        db_pool=db_pool,
        redis_client=redis_client,
        decrement_script_sha=decrement_script_sha,
        model=WorkerModel(),
    )


def _ensure_runtime() -> WorkerRuntime:
    """Lazily initialize singleton worker runtime with thread-safe guard."""
    global _runtime
    global _metrics_server_started

    if _runtime is not None:
        return _runtime

    with _runtime_lock:
        if _runtime is None:
            configure_logging()
            settings = _settings()
            _runtime = _bootstrap_runtime(settings)
            logger.info("worker_runtime_ready", metrics_port=settings.worker_metrics_port)
        if not _metrics_server_started:
            try:
                start_http_server(_settings().worker_metrics_port)
                _metrics_server_started = True
            except OSError:
                # Metrics server can already be bound by another worker lifecycle hook.
                _metrics_server_started = True

    return _runtime


def _runtime_or_raise() -> WorkerRuntime:
    """Return initialized runtime."""
    return _ensure_runtime()


def _decrement_active_sync(
    redis_client: Redis[str],
    *,
    script_sha: str,
    user_id: UUID,
) -> str:
    """Decrement active-task counter and recover from Redis script cache eviction."""
    try:
        redis_client.evalsha(script_sha, 1, active_tasks_key(user_id))
    except NoScriptError:
        script_sha = str(redis_client.script_load(DECR_ACTIVE_CLAMP_LUA))
        redis_client.evalsha(script_sha, 1, active_tasks_key(user_id))
    return script_sha


async def _run_redis_write_with_retry(
    *,
    context: TaskExecutionContext,
    operation_name: str,
    operation: Callable[[], Awaitable[object]],
) -> bool:
    try:
        await retry_async(
            operation,
            attempts=context.retry_attempts,
            base_delay_seconds=context.retry_base_delay_seconds,
            max_delay_seconds=context.retry_max_delay_seconds,
        )
    except Exception as exc:
        logger.warning(
            "worker_redis_write_failed",
            operation=operation_name,
            task_id=context.task_id,
            user_id=context.user_id,
            error=str(exc),
        )
        return False
    return True


async def _decrement_active_with_retry(context: TaskExecutionContext) -> str | None:
    async def _operation() -> str:
        return _decrement_active_sync(
            context.runtime.redis_client,
            script_sha=context.runtime.decrement_script_sha,
            user_id=context.user_uuid,
        )

    try:
        return await retry_async(
            _operation,
            attempts=context.retry_attempts,
            base_delay_seconds=context.retry_base_delay_seconds,
            max_delay_seconds=context.retry_max_delay_seconds,
        )
    except Exception as exc:
        logger.warning(
            "worker_redis_write_failed",
            operation="decrement_active",
            task_id=context.task_id,
            user_id=context.user_id,
            error=str(exc),
        )
        return None


async def _execute_task(context: TaskExecutionContext) -> dict[str, int]:
    started = await asyncio.wait_for(
        update_task_running(context.runtime.db_pool, context.task_uuid),
        timeout=context.settings.worker_db_timeout_seconds,
    )
    if not started:
        # Another path already moved this task to terminal; treat as no-op, not failure.
        TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()
        logger.info(
            "task_not_started_due_to_terminal_state",
            task_id=context.task_id,
            user_id=context.user_id,
        )
        return {"z": 0}

    start = time.perf_counter()
    result_value = context.runtime.model(context.x, context.y)
    runtime_ms = int((time.perf_counter() - start) * 1000)
    result_payload = {"z": result_value}

    completed = await asyncio.wait_for(
        update_task_completed(
            context.runtime.db_pool,
            task_id=context.task_uuid,
            result_payload=result_payload,
            runtime_ms=runtime_ms,
        ),
        timeout=context.settings.worker_db_timeout_seconds,
    )
    if not completed:
        # Lost the transition race (e.g., cancel/fail won).
        # Preserve winner and skip writes/refunds here.
        TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()
        logger.info(
            "task_completion_skipped_due_to_state_transition",
            task_id=context.task_id,
            user_id=context.user_id,
        )
        return result_payload

    expires_at = datetime.now(tz=UTC) + timedelta(seconds=context.settings.task_result_ttl_seconds)
    await _run_redis_write_with_retry(
        context=context,
        operation_name="result_cache_hset_completed",
        operation=lambda: asyncio.to_thread(
            lambda: context.runtime.redis_client.hset(
                result_cache_key(context.task_uuid),
                mapping={
                    "task_id": context.task_id,
                    "user_id": context.user_id,
                    "status": TaskStatus.COMPLETED,
                    "result": json.dumps(result_payload),
                    "error": "",
                    "queue_position": "",
                    "estimated_seconds": "",
                    "expires_at": expires_at.isoformat(),
                },
            )
        ),
    )
    await _run_redis_write_with_retry(
        context=context,
        operation_name="result_cache_expire_completed",
        operation=lambda: asyncio.to_thread(
            context.runtime.redis_client.expire,
            result_cache_key(context.task_uuid),
            context.settings.task_result_ttl_seconds,
        ),
    )

    decrement_sha = await _decrement_active_with_retry(context)
    if decrement_sha is not None:
        context.runtime.decrement_script_sha = decrement_sha
    TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.COMPLETED).inc()

    logger.info(
        "task_completed", task_id=context.task_id, user_id=context.user_id, runtime_ms=runtime_ms
    )
    return result_payload


async def _fail_terminal(context: TaskExecutionContext, error_message: str) -> None:
    async with (
        _acquire_db_connection(context.runtime.db_pool) as connection,
        connection.transaction(),
    ):
        failed = await asyncio.wait_for(
            update_task_failed(connection, task_id=context.task_uuid, error=error_message),
            timeout=context.settings.worker_db_timeout_seconds,
        )
        if not failed:
            TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()
            logger.info(
                "task_failure_refund_skipped_due_to_state_transition",
                task_id=context.task_id,
                user_id=context.user_id,
            )
            return
        await asyncio.wait_for(
            insert_credit_transaction(
                connection,
                user_id=context.user_uuid,
                task_id=context.task_uuid,
                delta=context.cost,
                reason="failure_refund",
            ),
            timeout=context.settings.worker_db_timeout_seconds,
        )

    await _run_redis_write_with_retry(
        context=context,
        operation_name="failure_refund_incrby",
        operation=lambda: asyncio.to_thread(
            context.runtime.redis_client.incrby,
            credits_cache_key(context.user_uuid),
            context.cost,
        ),
    )
    await _run_redis_write_with_retry(
        context=context,
        operation_name="failure_refund_dirty_sadd",
        operation=lambda: asyncio.to_thread(
            context.runtime.redis_client.sadd,
            "credits:dirty",
            credits_cache_key(context.user_uuid),
        ),
    )
    decrement_sha = await _decrement_active_with_retry(context)
    if decrement_sha is not None:
        context.runtime.decrement_script_sha = decrement_sha

    expires_at = datetime.now(tz=UTC) + timedelta(seconds=context.settings.task_result_ttl_seconds)
    await _run_redis_write_with_retry(
        context=context,
        operation_name="result_cache_hset_failed",
        operation=lambda: asyncio.to_thread(
            lambda: context.runtime.redis_client.hset(
                result_cache_key(context.task_uuid),
                mapping={
                    "task_id": context.task_id,
                    "user_id": context.user_id,
                    "status": TaskStatus.FAILED,
                    "result": "",
                    "error": error_message,
                    "queue_position": "",
                    "estimated_seconds": "",
                    "expires_at": expires_at.isoformat(),
                },
            )
        ),
    )
    await _run_redis_write_with_retry(
        context=context,
        operation_name="result_cache_expire_failed",
        operation=lambda: asyncio.to_thread(
            context.runtime.redis_client.expire,
            result_cache_key(context.task_uuid),
            context.settings.task_result_ttl_seconds,
        ),
    )
    TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.FAILED).inc()


@worker_process_init.connect
def _initialize_worker(**_: object) -> None:
    """Celery hook to warm runtime before task handling."""
    _ensure_runtime()


@worker_process_shutdown.connect
def _shutdown_worker(**_: object) -> None:
    """Celery hook for deterministic pool/loop/client shutdown."""
    global _runtime
    global _metrics_server_started
    runtime = _runtime
    if runtime is None:
        return

    logger.info("worker_runtime_shutdown_start")
    try:
        if not runtime.event_loop.is_closed():
            try:
                _run_coroutine_on_worker_loop(
                    runtime.event_loop,
                    runtime.db_pool.close(),
                    timeout_seconds=_settings().worker_loop_shutdown_timeout_seconds,
                )
            finally:
                _stop_event_loop(
                    runtime.event_loop,
                    loop_thread=runtime.loop_thread,
                    timeout_seconds=_settings().worker_loop_shutdown_timeout_seconds,
                )
    finally:
        runtime.redis_client.close()
        runtime.redis_client.connection_pool.disconnect()
        _runtime = None
        _metrics_server_started = False
    logger.info("worker_runtime_shutdown_complete")


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5, name="solution0.run_task")
def run_task(
    self: Task,
    task_id: str,
    x: int,
    y: int,
    cost: int,
    user_id: str,
    api_key: str,
    trace_id: str | None = None,
) -> dict[str, int]:
    """Execute one task with guarded state transitions and failure compensation."""
    settings = _settings()
    runtime = _runtime_or_raise()
    clear_log_context()
    bind_log_context(trace_id=trace_id or "", task_id=task_id, user_id=user_id)
    context = TaskExecutionContext(
        runtime=runtime,
        settings=settings,
        task_id=task_id,
        user_id=user_id,
        task_uuid=UUID(task_id),
        user_uuid=UUID(user_id),
        x=x,
        y=y,
        cost=cost,
        api_key=api_key,
        retry_attempts=int(getattr(settings, "redis_retry_attempts", 3)),
        retry_base_delay_seconds=float(getattr(settings, "redis_retry_base_delay_seconds", 0.05)),
        retry_max_delay_seconds=float(getattr(settings, "redis_retry_max_delay_seconds", 0.5)),
    )

    try:
        return _run_coroutine_on_worker_loop(
            runtime.event_loop,
            _execute_task(context),
            timeout_seconds=settings.worker_loop_task_timeout_seconds,
        )
    except Exception as exc:
        logger.exception("task_execution_error", task_id=task_id, user_id=user_id, error=str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc

        try:
            _run_coroutine_on_worker_loop(
                runtime.event_loop,
                _fail_terminal(context, str(exc)),
                timeout_seconds=settings.worker_loop_task_timeout_seconds,
            )
        except Exception as terminal_exc:
            logger.exception(
                "task_terminal_failure_handler_error",
                task_id=task_id,
                user_id=user_id,
                error=str(terminal_exc),
            )
        return {"z": 0}
    finally:
        clear_log_context()
