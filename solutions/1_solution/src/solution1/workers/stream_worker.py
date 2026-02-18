"""Redis Streams worker runtime for Solution 1 execution path."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import asyncpg
from opentelemetry.trace import SpanKind
from prometheus_client import start_http_server
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from solution1.constants import (
    ModelClass,
    TaskCompletionMetricStatus,
    TaskStatus,
    runtime_seconds_for_model,
)
from solution1.core.settings import AppSettings, load_settings
from solution1.db.migrate import run_migrations
from solution1.db.repository import (
    get_task,
    insert_credit_transaction,
    update_task_completed,
    update_task_failed,
    update_task_running,
    upsert_stream_checkpoint,
)
from solution1.observability.metrics import (
    PEL_RECOVERY_TOTAL,
    STREAM_CHECKPOINT_UPDATES_TOTAL,
    STREAM_CONSUMER_LAG,
    STREAM_PENDING_ENTRIES,
    TASK_COMPLETIONS_TOTAL,
)
from solution1.observability.tracing import configure_process_tracing, start_span
from solution1.services.auth import pending_marker_key, result_cache_key, task_state_key
from solution1.services.billing import decrement_active_counter, refund_and_decrement_active
from solution1.services.webhooks import (
    build_terminal_webhook_event,
    enqueue_terminal_webhook_event,
)
from solution1.utils.logging import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)
from solution1.utils.lua_scripts import DECR_ACTIVE_CLAMP_LUA
from solution1.utils.retry import retry_async

logger = get_logger("solution1.workers.stream")


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
    """Simulated model from assignment baseline."""

    def __init__(self) -> None:
        self._warmed = False

    async def warmup(self) -> None:
        """Run one-time model warmup without blocking the event loop."""
        if self._warmed:
            return
        logger.info("worker_initializing")
        await asyncio.sleep(10)
        self._warmed = True
        logger.info("worker_initialized")

    def __call__(self, x: int, y: int, model_class: ModelClass) -> int:
        if not self._warmed:
            raise RuntimeError("worker model must be warmed before use")
        logger.info("worker_processing", x=x, y=y, model_class=model_class.value)
        time.sleep(runtime_seconds_for_model(model_class))
        return x + y


@dataclass(frozen=True)
class StreamMessage:
    message_id: str
    task_id: UUID
    user_id: UUID
    cost: int
    model_class: ModelClass
    x: int
    y: int
    trace_id: str
    trace_context: dict[str, str]


@dataclass
class StreamWorkerRuntime:
    settings: AppSettings
    db_pool: asyncpg.Pool
    redis_client: Redis[str]
    decrement_script_sha: str
    model: WorkerModel
    consumer_name: str


def _consumer_name(settings: AppSettings) -> str:
    hostname = socket.gethostname()
    return f"{settings.app_name}-{hostname}-{os.getpid()}"


def _parse_message_payload(fields: dict[str, str]) -> StreamMessage | None:
    task_id_text = fields.get("task_id")
    payload_text = fields.get("payload", "{}")
    user_id_text = fields.get("user_id")
    cost_text = fields.get("cost")

    if task_id_text is None:
        return None

    try:
        payload: dict[str, object] = json.loads(payload_text)
    except json.JSONDecodeError:
        return None

    payload_user_id = payload.get("user_id")
    payload_cost = payload.get("cost")

    resolved_user_id = user_id_text or (
        str(payload_user_id) if isinstance(payload_user_id, str) else None
    )
    if resolved_user_id is None:
        return None

    x_raw = payload.get("x")
    y_raw = payload.get("y")
    if not isinstance(x_raw, int) or not isinstance(y_raw, int):
        return None

    trace_raw = payload.get("trace_id")
    trace_id = trace_raw if isinstance(trace_raw, str) else ""
    trace_context_raw = payload.get("trace_context")
    trace_context: dict[str, str] = {}
    if isinstance(trace_context_raw, dict):
        for key, value in trace_context_raw.items():
            if isinstance(key, str) and isinstance(value, str):
                trace_context[key] = value
    model_class_raw = payload.get("model_class")
    if isinstance(model_class_raw, str):
        try:
            model_class = ModelClass(model_class_raw)
        except ValueError:
            return None
    else:
        model_class = ModelClass.SMALL

    resolved_cost_raw: int | str
    if isinstance(cost_text, str):
        resolved_cost_raw = cost_text
    elif isinstance(payload_cost, int | str):
        resolved_cost_raw = payload_cost
    else:
        resolved_cost_raw = 0

    try:
        return StreamMessage(
            message_id="",
            task_id=UUID(task_id_text),
            user_id=UUID(resolved_user_id),
            cost=int(resolved_cost_raw),
            model_class=model_class,
            x=x_raw,
            y=y_raw,
            trace_id=trace_id,
            trace_context=trace_context,
        )
    except (TypeError, ValueError):
        return None


def _stream_message_age_seconds(message_id: str) -> float | None:
    """Return stream message age in seconds based on Redis stream ID timestamp."""
    head, _, _tail = message_id.partition("-")
    try:
        message_ms = int(head)
    except ValueError:
        return None
    age_ms = max(0, int(time.time() * 1000) - message_ms)
    return age_ms / 1000.0


async def _ensure_consumer_group(runtime: StreamWorkerRuntime) -> None:
    settings = runtime.settings
    try:
        await runtime.redis_client.xgroup_create(
            name=settings.redis_tasks_stream_key,
            groupname=settings.redis_tasks_stream_group,
            id="0-0",
            mkstream=True,
        )
        logger.info(
            "stream_consumer_group_created",
            stream_key=settings.redis_tasks_stream_key,
            group=settings.redis_tasks_stream_group,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _set_worker_heartbeat(runtime: StreamWorkerRuntime) -> None:
    await runtime.redis_client.setex(
        runtime.settings.stream_worker_heartbeat_key,
        runtime.settings.stream_worker_heartbeat_ttl_seconds,
        str(int(time.time())),
    )


async def _refresh_stream_group_metrics(runtime: StreamWorkerRuntime) -> None:
    """Refresh stream lag and pending-entry metrics for the configured consumer group."""
    group_name = runtime.settings.redis_tasks_stream_group
    pending_entries = 0
    lag = 0

    try:
        groups = await runtime.redis_client.xinfo_groups(  # type: ignore[no-untyped-call]
            runtime.settings.redis_tasks_stream_key
        )
    except Exception:
        return

    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            current_name = group.get("name")
            if str(current_name) != group_name:
                continue
            pending_entries = int(group.get("pending", 0) or 0)
            lag_raw = group.get("lag")
            lag = int(lag_raw) if lag_raw is not None else 0
            break

    STREAM_PENDING_ENTRIES.labels(group=group_name).set(max(0, pending_entries))
    STREAM_CONSUMER_LAG.labels(group=group_name).set(max(0, lag))


async def _read_new_messages(runtime: StreamWorkerRuntime) -> list[tuple[str, dict[str, str]]]:
    settings = runtime.settings
    try:
        raw = await runtime.redis_client.xreadgroup(
            groupname=settings.redis_tasks_stream_group,
            consumername=runtime.consumer_name,
            streams={settings.redis_tasks_stream_key: ">"},
            count=settings.stream_worker_read_count,
            block=settings.stream_worker_block_ms,
        )
    except ResponseError as exc:
        if "NOGROUP" not in str(exc):
            raise
        await _ensure_consumer_group(runtime)
        return []
    if not raw:
        return []

    entries: list[tuple[str, dict[str, str]]] = []
    for _stream, stream_entries in raw:
        for message_id, fields in stream_entries:
            entries.append((str(message_id), dict(fields)))
    return entries


async def _claim_idle_messages(
    runtime: StreamWorkerRuntime,
    *,
    start_id: str,
) -> tuple[str, list[tuple[str, dict[str, str]]]]:
    settings = runtime.settings
    try:
        raw = await runtime.redis_client.xautoclaim(
            name=settings.redis_tasks_stream_key,
            groupname=settings.redis_tasks_stream_group,
            consumername=runtime.consumer_name,
            min_idle_time=settings.stream_worker_claim_idle_ms,
            start_id=start_id,
            count=settings.stream_worker_claim_count,
        )
    except ResponseError:
        # Group may not exist yet during start races.
        await _ensure_consumer_group(runtime)
        return start_id, []

    next_start = start_id
    claimed: list[tuple[str, dict[str, str]]] = []

    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        next_start = str(raw[0])
        claimed_entries = raw[1]
        if isinstance(claimed_entries, list):
            for entry in claimed_entries:
                if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                    continue
                message_id = str(entry[0])
                fields = entry[1]
                if isinstance(fields, dict):
                    claimed.append((message_id, {str(k): str(v) for k, v in fields.items()}))

    return next_start, claimed


async def _ack_message(runtime: StreamWorkerRuntime, message_id: str) -> None:
    await runtime.redis_client.xack(  # type: ignore[no-untyped-call]
        runtime.settings.redis_tasks_stream_key,
        runtime.settings.redis_tasks_stream_group,
        message_id,
    )


async def _run_redis_write_with_retry(
    runtime: StreamWorkerRuntime,
    *,
    operation_name: str,
    operation: Callable[[], Awaitable[object]],
) -> bool:
    attempts = int(getattr(runtime.settings, "redis_retry_attempts", 3))
    base_delay_seconds = float(getattr(runtime.settings, "redis_retry_base_delay_seconds", 0.05))
    max_delay_seconds = float(getattr(runtime.settings, "redis_retry_max_delay_seconds", 0.5))
    try:
        await retry_async(
            operation,
            attempts=attempts,
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
        )
    except Exception as exc:
        logger.warning(
            "stream_worker_redis_write_failed",
            operation=operation_name,
            error=str(exc),
        )
        return False
    return True


async def _persist_stream_checkpoint(runtime: StreamWorkerRuntime, last_stream_id: str) -> None:
    try:
        await upsert_stream_checkpoint(
            runtime.db_pool,
            consumer_group=runtime.settings.redis_tasks_stream_group,
            last_stream_id=last_stream_id,
        )
        STREAM_CHECKPOINT_UPDATES_TOTAL.labels(result="ok").inc()
    except Exception as exc:
        STREAM_CHECKPOINT_UPDATES_TOTAL.labels(result="error").inc()
        logger.warning(
            "stream_checkpoint_persist_failed",
            consumer_group=runtime.settings.redis_tasks_stream_group,
            last_stream_id=last_stream_id,
            error=str(exc),
        )


async def _update_task_state(
    runtime: StreamWorkerRuntime,
    *,
    task_id: UUID,
    status: TaskStatus,
    error: str | None = None,
) -> None:
    mapping: dict[str, str] = {"status": status.value}
    now_epoch = str(int(time.time()))
    if status == TaskStatus.RUNNING:
        mapping["started_at_epoch"] = now_epoch
    elif status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        mapping["completed_at_epoch"] = now_epoch
    if error is not None:
        mapping["error"] = error
    await runtime.redis_client.hset(
        task_state_key(task_id),
        mapping=cast(Mapping[str | bytes, bytes | float | int | str], mapping),
    )
    await runtime.redis_client.expire(
        task_state_key(task_id),
        runtime.settings.redis_task_state_ttl_seconds,
    )


async def _store_result_cache(
    runtime: StreamWorkerRuntime,
    *,
    message: StreamMessage,
    status: TaskStatus,
    result_payload: dict[str, int] | None,
    error: str,
) -> None:
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=runtime.settings.task_result_ttl_seconds)
    await runtime.redis_client.hset(
        result_cache_key(message.task_id),
        mapping={
            "task_id": str(message.task_id),
            "user_id": str(message.user_id),
            "status": status.value,
            "result": json.dumps(result_payload) if result_payload is not None else "",
            "error": error,
            "queue_position": "",
            "estimated_seconds": "",
            "expires_at": expires_at.isoformat(),
        },
    )
    await runtime.redis_client.expire(
        result_cache_key(message.task_id),
        runtime.settings.task_result_ttl_seconds,
    )


async def _handle_not_started_message(
    runtime: StreamWorkerRuntime,
    *,
    message: StreamMessage,
) -> None:
    current_task = await get_task(runtime.db_pool, message.task_id)
    if current_task is None:
        pending_key = pending_marker_key(message.task_id)
        try:
            marker_exists = bool(await runtime.redis_client.exists(pending_key))
        except Exception as exc:
            logger.warning(
                "task_row_missing_marker_check_failed",
                task_id=str(message.task_id),
                stream_message_id=message.message_id,
                error=str(exc),
            )
            return
        if marker_exists:
            logger.info(
                "task_row_missing_retry_later",
                task_id=str(message.task_id),
                stream_message_id=message.message_id,
            )
            # API writes to stream before PG persist by design.
            # Retry only while pending marker still exists.
            return

        age_seconds = _stream_message_age_seconds(message.message_id)
        if age_seconds is None or (age_seconds < runtime.settings.orphan_marker_timeout_seconds):
            logger.info(
                "task_row_missing_retry_later",
                task_id=str(message.task_id),
                stream_message_id=message.message_id,
                reason="marker_missing_within_grace",
                age_seconds=age_seconds,
            )
            return

        # Stream entry outlived API persist window and marker has expired/been removed.
        # Drop orphan message and clear admission task hash artifact.
        try:
            await runtime.redis_client.delete(task_state_key(message.task_id))
        except Exception as exc:
            logger.warning(
                "task_orphan_state_cleanup_failed",
                task_id=str(message.task_id),
                stream_message_id=message.message_id,
                error=str(exc),
            )
        TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()
        await _ack_message(runtime, message.message_id)
        logger.warning(
            "task_stream_orphan_dropped",
            task_id=str(message.task_id),
            stream_message_id=message.message_id,
            age_seconds=age_seconds,
        )
        return

    if current_task.status == TaskStatus.PENDING:
        logger.info(
            "task_pending_retry_later",
            task_id=str(message.task_id),
            stream_message_id=message.message_id,
        )
        return

    TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()
    await _ack_message(runtime, message.message_id)
    logger.info("task_skip_non_pending", task_id=str(message.task_id))


def _build_stream_message(*, message_id: str, parsed: StreamMessage) -> StreamMessage:
    return StreamMessage(
        message_id=message_id,
        task_id=parsed.task_id,
        user_id=parsed.user_id,
        cost=parsed.cost,
        model_class=parsed.model_class,
        x=parsed.x,
        y=parsed.y,
        trace_id=parsed.trace_id,
        trace_context=parsed.trace_context,
    )


async def _process_started_message(
    runtime: StreamWorkerRuntime,
    *,
    message: StreamMessage,
) -> str:
    started = await update_task_running(runtime.db_pool, message.task_id)
    if not started:
        await _handle_not_started_message(runtime, message=message)
        return runtime.decrement_script_sha

    await _update_task_state(runtime, task_id=message.task_id, status=TaskStatus.RUNNING)

    started_at = time.perf_counter()
    result_value = await asyncio.to_thread(
        runtime.model,
        message.x,
        message.y,
        message.model_class,
    )
    runtime_ms = int((time.perf_counter() - started_at) * 1000)
    result_payload = {"z": result_value}

    completed = await update_task_completed(
        runtime.db_pool,
        task_id=message.task_id,
        result_payload=result_payload,
        runtime_ms=runtime_ms,
    )
    if not completed:
        TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()
        await _ack_message(runtime, message.message_id)
        logger.info("task_completion_skipped_due_to_state_transition")
        return runtime.decrement_script_sha

    await _run_redis_write_with_retry(
        runtime,
        operation_name="result_cache_store_completed",
        operation=lambda: _store_result_cache(
            runtime,
            message=message,
            status=TaskStatus.COMPLETED,
            result_payload=result_payload,
            error="",
        ),
    )
    await _run_redis_write_with_retry(
        runtime,
        operation_name="task_state_completed",
        operation=lambda: _update_task_state(
            runtime,
            task_id=message.task_id,
            status=TaskStatus.COMPLETED,
        ),
    )

    decrement_sha: str | None = None

    async def _capture_decrement_sha() -> None:
        nonlocal decrement_sha
        decrement_sha = await decrement_active_counter(
            redis_client=runtime.redis_client,
            decrement_script_sha=runtime.decrement_script_sha,
            user_id=message.user_id,
        )

    decrement_ok = await _run_redis_write_with_retry(
        runtime,
        operation_name="active_counter_decrement_completed",
        operation=_capture_decrement_sha,
    )
    if decrement_ok and decrement_sha is not None:
        runtime.decrement_script_sha = decrement_sha

    if bool(getattr(runtime.settings, "webhook_enabled", True)):
        try:
            await enqueue_terminal_webhook_event(
                redis_client=runtime.redis_client,
                queue_key=str(getattr(runtime.settings, "webhook_queue_key", "webhook:queue")),
                event=build_terminal_webhook_event(
                    user_id=message.user_id,
                    task_id=message.task_id,
                    status=TaskStatus.COMPLETED.value,
                    result=result_payload,
                    error=None,
                ),
                max_queue_length=int(getattr(runtime.settings, "webhook_queue_maxlen", 100000)),
            )
        except Exception as webhook_exc:
            logger.warning(
                "webhook_event_enqueue_failed",
                task_id=str(message.task_id),
                event_status=TaskStatus.COMPLETED.value,
                error=str(webhook_exc),
            )

    TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.COMPLETED).inc()
    await _ack_message(runtime, message.message_id)
    logger.info("task_completed", runtime_ms=runtime_ms)
    return runtime.decrement_script_sha


async def _process_message(
    runtime: StreamWorkerRuntime,
    *,
    message_id: str,
    fields: dict[str, str],
) -> str:
    parsed = _parse_message_payload(fields)
    if parsed is None:
        logger.warning("stream_message_invalid", message_id=message_id)
        await _ack_message(runtime, message_id)
        return runtime.decrement_script_sha

    message = _build_stream_message(message_id=message_id, parsed=parsed)

    clear_log_context()
    bind_log_context(
        trace_id=message.trace_id,
        task_id=str(message.task_id),
        user_id=str(message.user_id),
        stream_message_id=message.message_id,
    )

    try:
        with start_span(
            tracer_name="solution1.worker",
            span_name="stream_worker.process_message",
            kind=SpanKind.CONSUMER,
            parent_carrier=message.trace_context,
            attributes={
                "task.id": str(message.task_id),
                "task.model_class": message.model_class.value,
                "task.cost": message.cost,
                "stream.message_id": message.message_id,
            },
        ):
            return await _process_started_message(runtime, message=message)
    except Exception as exc:
        return await _handle_failure(runtime=runtime, message=message, error=exc)
    finally:
        clear_log_context()


async def _handle_failure(
    *,
    runtime: StreamWorkerRuntime,
    message: StreamMessage,
    error: Exception,
) -> str:
    logger.exception("stream_task_execution_failed", error=str(error))

    failed = False
    should_refund = False
    try:
        async with (
            _acquire_db_connection(runtime.db_pool) as connection,
            connection.transaction(),
        ):
            failed = await update_task_failed(
                connection,
                task_id=message.task_id,
                error=str(error),
            )
            if failed:
                await insert_credit_transaction(
                    connection,
                    user_id=message.user_id,
                    task_id=message.task_id,
                    delta=message.cost,
                    reason="failure_refund",
                )
                should_refund = True
    except Exception as db_exc:
        logger.exception("stream_task_failure_db_update_failed", error=str(db_exc))
        should_refund = False

    if should_refund:
        decrement_sha: str | None = None

        async def _refund_and_decrement() -> str:
            return await refund_and_decrement_active(
                redis_client=runtime.redis_client,
                decrement_script_sha=runtime.decrement_script_sha,
                user_id=message.user_id,
                amount=message.cost,
            )

        async def _capture_refund_decrement_sha() -> None:
            nonlocal decrement_sha
            decrement_sha = await _refund_and_decrement()

        decrement_ok = await _run_redis_write_with_retry(
            runtime,
            operation_name="active_counter_refund_and_decrement_failed",
            operation=_capture_refund_decrement_sha,
        )
        if decrement_ok and decrement_sha is not None:
            runtime.decrement_script_sha = decrement_sha

        await _run_redis_write_with_retry(
            runtime,
            operation_name="result_cache_store_failed",
            operation=lambda: _store_result_cache(
                runtime,
                message=message,
                status=TaskStatus.FAILED,
                result_payload=None,
                error=str(error),
            ),
        )
        await _run_redis_write_with_retry(
            runtime,
            operation_name="task_state_failed",
            operation=lambda: _update_task_state(
                runtime,
                task_id=message.task_id,
                status=TaskStatus.FAILED,
                error=str(error),
            ),
        )
        if bool(getattr(runtime.settings, "webhook_enabled", True)):
            try:
                await enqueue_terminal_webhook_event(
                    redis_client=runtime.redis_client,
                    queue_key=str(getattr(runtime.settings, "webhook_queue_key", "webhook:queue")),
                    event=build_terminal_webhook_event(
                        user_id=message.user_id,
                        task_id=message.task_id,
                        status=TaskStatus.FAILED.value,
                        result=None,
                        error=str(error),
                    ),
                    max_queue_length=int(getattr(runtime.settings, "webhook_queue_maxlen", 100000)),
                )
            except Exception as webhook_exc:
                logger.warning(
                    "webhook_event_enqueue_failed",
                    task_id=str(message.task_id),
                    event_status=TaskStatus.FAILED.value,
                    error=str(webhook_exc),
                )
        TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.FAILED).inc()
    else:
        TASK_COMPLETIONS_TOTAL.labels(status=TaskCompletionMetricStatus.SKIPPED).inc()

    await _ack_message(runtime, message.message_id)
    return runtime.decrement_script_sha


async def main_async() -> None:
    configure_logging()
    settings = load_settings()
    base_service_name = str(getattr(settings, "app_name", "mc-solution1"))
    configure_process_tracing(
        settings=settings,
        service_name=f"{base_service_name}-worker",
    )

    await run_migrations(str(settings.postgres_dsn))
    db_pool = await asyncpg.create_pool(
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
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        max_connections=50,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    decrement_script_sha = str(
        await redis_client.script_load(DECR_ACTIVE_CLAMP_LUA)  # type: ignore[no-untyped-call]
    )

    model = WorkerModel()
    await model.warmup()

    runtime = StreamWorkerRuntime(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        decrement_script_sha=decrement_script_sha,
        model=model,
        consumer_name=_consumer_name(settings),
    )

    await _ensure_consumer_group(runtime)

    try:
        start_http_server(settings.worker_metrics_port)
    except OSError:
        logger.warning("worker_metrics_port_in_use", port=settings.worker_metrics_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("stream_worker_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    claim_cursor = "0-0"

    try:
        while not stop_event.is_set():
            try:
                await _set_worker_heartbeat(runtime)
                await _refresh_stream_group_metrics(runtime)

                claim_cursor, claimed_messages = await _claim_idle_messages(
                    runtime,
                    start_id=claim_cursor,
                )
                if claimed_messages:
                    PEL_RECOVERY_TOTAL.inc(len(claimed_messages))
                    for message_id, fields in claimed_messages:
                        runtime.decrement_script_sha = await _process_message(
                            runtime,
                            message_id=message_id,
                            fields=fields,
                        )
                        await _persist_stream_checkpoint(runtime, message_id)
                    continue

                new_messages = await _read_new_messages(runtime)
                for message_id, fields in new_messages:
                    runtime.decrement_script_sha = await _process_message(
                        runtime,
                        message_id=message_id,
                        fields=fields,
                    )
                    await _persist_stream_checkpoint(runtime, message_id)
            except Exception as exc:
                logger.warning("stream_worker_iteration_error", error=str(exc))
                await asyncio.sleep(runtime.settings.stream_worker_error_backoff_seconds)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await redis_client.close()
        await db_pool.close()
        logger.info("stream_worker_shutdown_complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
