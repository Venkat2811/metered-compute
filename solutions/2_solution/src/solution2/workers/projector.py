"""RabbitMQ-backed query projector for Solution 2."""

from __future__ import annotations

import asyncio
import json
import signal
import time
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import asyncpg
from prometheus_client import start_http_server
from redis.asyncio import Redis

from solution2.constants import TaskStatus, resolve_queue
from solution2.core.settings import AppSettings, load_settings
from solution2.db.migrate import run_migrations
from solution2.db.repository import (
    get_task_command,
    record_inbox_event,
    upsert_task_query_view,
)
from solution2.observability.metrics import EVENTS_PROJECTED_TOTAL, PROJECTION_LAG_SECONDS
from solution2.observability.tracing import configure_process_tracing, start_span
from solution2.services.auth import task_state_key
from solution2.utils.logging import configure_logging, get_logger
from solution2.workers.worker import RabbitMQDelivery, RabbitMQTaskConsumer

logger = get_logger("solution2.workers.projector")

PROJECTOR_CONSUMER_NAME = "projector"
PROJECTOR_QUEUE = "queue.projector"
TASK_SUBMITTED_EVENT = "task.submitted"
TASK_COMPLETED_EVENT = "task.completed"
TASK_FAILED_EVENT = "task.failed"
TASK_CANCELLED_EVENT = "task.cancelled"
TASK_TIMED_OUT_EVENT = "task.timed_out"

_EVENT_TO_STATUS: dict[str, TaskStatus] = {
    TASK_SUBMITTED_EVENT: TaskStatus.PENDING,
    TASK_COMPLETED_EVENT: TaskStatus.COMPLETED,
    TASK_FAILED_EVENT: TaskStatus.FAILED,
    TASK_CANCELLED_EVENT: TaskStatus.CANCELLED,
    TASK_TIMED_OUT_EVENT: TaskStatus.TIMEOUT,
}


@dataclass(frozen=True)
class ProjectionEvent:
    """Projection event extracted from RabbitMQ payload."""

    event_id: UUID
    task_id: UUID
    event_type: str
    status: TaskStatus
    result: dict[str, object] | None
    error: str | None
    queue_name: str | None
    runtime_ms: int | None
    occurred_at_epoch: int | None


@dataclass
class ProjectorRuntime:
    """Runtime dependencies for the projector process."""

    settings: AppSettings
    db_pool: asyncpg.Pool
    redis_client: Redis[str]
    consumer: RabbitMQTaskConsumer


def _metrics_port(settings: AppSettings) -> int:
    return int(getattr(settings, "projector_metrics_port", 9300))


def _idle_sleep_seconds(settings: AppSettings) -> float:
    return max(0.01, float(getattr(settings, "projector_idle_sleep_seconds", 0.05)))


def _error_backoff_seconds(settings: AppSettings) -> float:
    return max(0.05, float(getattr(settings, "projector_error_backoff_seconds", 1.0)))


def _coerce_epoch(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _resolve_status(*, event_type: str, payload: dict[str, object]) -> TaskStatus | None:
    raw_status = payload.get("status")
    if isinstance(raw_status, str):
        try:
            return TaskStatus(raw_status)
        except ValueError:
            return None
    return _EVENT_TO_STATUS.get(event_type)


def _parse_projection_event(delivery: RabbitMQDelivery) -> ProjectionEvent | None:
    try:
        payload = json.loads(delivery.body)
    except json.JSONDecodeError:
        logger.warning(
            "projector_payload_decode_failed",
            queue=delivery.queue_name,
            delivery_tag=delivery.delivery_tag,
        )
        return None

    if not isinstance(payload, dict):
        logger.warning(
            "projector_payload_invalid_shape",
            queue=delivery.queue_name,
            delivery_tag=delivery.delivery_tag,
        )
        return None

    try:
        event_id_raw = payload.get("event_id") or delivery.message_id
        if not isinstance(event_id_raw, str):
            raise ValueError("missing event_id")
        task_id = UUID(str(payload["task_id"]))
        event_type = str(payload.get("event_type", TASK_SUBMITTED_EVENT))
        status = _resolve_status(event_type=event_type, payload=payload)
        if status is None:
            return None

        result = payload.get("result")
        parsed_result = cast(dict[str, object], result) if isinstance(result, dict) else None
        error = str(payload["error"]) if isinstance(payload.get("error"), str) else None
        queue_name = str(payload["queue"]) if isinstance(payload.get("queue"), str) else None
        runtime_value = payload.get("runtime_ms")
        runtime_ms = int(runtime_value) if isinstance(runtime_value, (int, float, str)) else None
        occurred_at_epoch = _coerce_epoch(payload.get("occurred_at_epoch")) or _coerce_epoch(
            payload.get("created_at_epoch")
        )
        return ProjectionEvent(
            event_id=UUID(event_id_raw),
            task_id=task_id,
            event_type=event_type,
            status=status,
            result=parsed_result,
            error=error,
            queue_name=queue_name,
            runtime_ms=runtime_ms,
            occurred_at_epoch=occurred_at_epoch,
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "projector_payload_invalid",
            error=str(exc),
            queue=delivery.queue_name,
            delivery_tag=delivery.delivery_tag,
        )
        return None


async def _persist_projection(
    *,
    runtime: ProjectorRuntime,
    event: ProjectionEvent,
) -> tuple[bool, str | None, UUID | None]:
    async with runtime.db_pool.acquire() as connection, connection.transaction():
        inserted = await record_inbox_event(
            connection,
            event_id=event.event_id,
            consumer_name=PROJECTOR_CONSUMER_NAME,
        )
        if not inserted:
            return False, None, None

        command = await get_task_command(connection, event.task_id)
        if command is None:
            return False, None, None

        queue_name = event.queue_name or resolve_queue(
            tier=command.tier,
            mode=command.mode,
            model_class=command.model_class,
        )
        await upsert_task_query_view(
            connection,
            task_id=command.task_id,
            user_id=command.user_id,
            tier=command.tier,
            mode=command.mode,
            model_class=command.model_class.value,
            status=event.status,
            result=event.result,
            error=event.error,
            queue_name=queue_name,
            runtime_ms=event.runtime_ms,
        )
        return True, queue_name, command.user_id


async def _write_projection_cache(
    *,
    runtime: ProjectorRuntime,
    event: ProjectionEvent,
    queue_name: str | None,
    user_id: UUID,
) -> None:
    task_key = task_state_key(event.task_id)
    task_mapping: dict[str | bytes, str | int | float | bytes] = {
        "task_id": str(event.task_id),
        "user_id": str(user_id),
        "status": event.status.value,
        "queue": queue_name or "",
        "error": event.error or "",
    }
    if event.runtime_ms is not None:
        task_mapping["runtime_ms"] = int(event.runtime_ms)
    if event.result is not None:
        task_mapping["result"] = json.dumps(event.result, separators=(",", ":"))

    await runtime.redis_client.hset(task_key, mapping=task_mapping)
    await runtime.redis_client.expire(task_key, runtime.settings.redis_task_state_ttl_seconds)

    return


def _observe_projection_lag(event: ProjectionEvent) -> None:
    if event.occurred_at_epoch is None:
        return
    lag_seconds = max(0.0, time.time() - float(event.occurred_at_epoch))
    PROJECTION_LAG_SECONDS.labels(event_type=event.event_type).observe(lag_seconds)


async def _process_delivery(
    *,
    runtime: ProjectorRuntime,
    delivery: RabbitMQDelivery,
) -> tuple[str, str]:
    event = _parse_projection_event(delivery)
    if event is None:
        return "invalid", "unknown"

    with start_span(
        tracer_name="solution2.projector",
        span_name="projector.apply_event",
        attributes={
            "event.id": str(event.event_id),
            "event.type": event.event_type,
            "task.id": str(event.task_id),
        },
    ):
        projected, queue_name, user_id = await _persist_projection(runtime=runtime, event=event)
        if not projected or queue_name is None or user_id is None:
            return "skipped", event.event_type

        await _write_projection_cache(
            runtime=runtime,
            event=event,
            queue_name=queue_name,
            user_id=user_id,
        )
        _observe_projection_lag(event)
        return "projected", event.event_type


async def _build_db_pool(settings: AppSettings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=str(settings.postgres_dsn),
        min_size=settings.db_pool_min_size,
        max_size=max(2, min(settings.db_pool_max_size, 8)),
        command_timeout=settings.db_pool_command_timeout_seconds,
        server_settings={
            "statement_timeout": str(settings.db_statement_timeout_batch_ms),
            "idle_in_transaction_session_timeout": str(settings.db_idle_in_transaction_timeout_ms),
        },
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime_seconds,
    )


async def main_async() -> None:
    configure_logging()
    settings = load_settings()
    base_service_name = str(getattr(settings, "app_name", "mc-solution2"))
    configure_process_tracing(
        settings=settings,
        service_name=f"{base_service_name}-projector",
    )

    await run_migrations(str(settings.postgres_dsn))
    db_pool = await _build_db_pool(settings)
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    consumer = RabbitMQTaskConsumer(
        rabbitmq_url=settings.rabbitmq_url,
        socket_connect_timeout=float(getattr(settings, "worker_db_timeout_seconds", 3.0)),
    )

    runtime = ProjectorRuntime(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        consumer=consumer,
    )

    try:
        await redis_client.ping()
        await asyncio.to_thread(consumer.connect)
        await asyncio.to_thread(consumer.ensure_queues, queue_names=(PROJECTOR_QUEUE,))
    except Exception as exc:
        logger.exception("projector_startup_failed", error=str(exc))
        consumer.close()
        await redis_client.close()
        await db_pool.close()
        return

    try:
        start_http_server(_metrics_port(settings))
    except OSError as exc:
        logger.warning(
            "projector_metrics_port_in_use",
            port=_metrics_port(settings),
            error=str(exc),
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("projector_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        while not stop_event.is_set():
            delivery = await asyncio.to_thread(
                runtime.consumer.get_one,
                queue_names=(PROJECTOR_QUEUE,),
            )
            if delivery is None:
                await asyncio.sleep(_idle_sleep_seconds(settings))
                continue

            try:
                result, event_type = await _process_delivery(runtime=runtime, delivery=delivery)
                EVENTS_PROJECTED_TOTAL.labels(
                    event_type=event_type,
                    result=result,
                ).inc()
                await asyncio.to_thread(
                    runtime.consumer.ack,
                    delivery_tag=delivery.delivery_tag,
                )
            except Exception as exc:
                EVENTS_PROJECTED_TOTAL.labels(event_type="unknown", result="error").inc()
                logger.exception(
                    "projector_delivery_failed",
                    error=str(exc),
                    queue=delivery.queue_name,
                    delivery_tag=delivery.delivery_tag,
                )
                await asyncio.to_thread(
                    runtime.consumer.nack,
                    delivery_tag=delivery.delivery_tag,
                    requeue=True,
                )
                await asyncio.sleep(_error_backoff_seconds(settings))
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        consumer.close()
        await redis_client.close()
        await db_pool.close()
        logger.info("projector_shutdown_complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
