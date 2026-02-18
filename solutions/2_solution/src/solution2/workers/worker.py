"""RabbitMQ-backed worker execution loop for Solution 2."""

from __future__ import annotations

import asyncio
import json
import signal
import time
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import asyncpg
from prometheus_client import start_http_server
from redis.asyncio import Redis

from solution2.constants import (
    ModelClass,
    ReservationState,
    TaskCompletionMetricStatus,
    TaskStatus,
    runtime_seconds_for_model,
)
from solution2.core.settings import AppSettings, load_settings
from solution2.db.migrate import run_migrations
from solution2.db.repository import (
    add_user_credits,
    capture_reservation,
    create_outbox_event,
    get_credit_reservation,
    get_task_command,
    insert_credit_transaction,
    record_inbox_event,
    release_reservation,
    update_task_command_completed,
    update_task_command_failed,
    update_task_command_running,
)
from solution2.observability.metrics import (
    RABBITMQ_QUEUE_DEPTH,
    RESERVATIONS_ACTIVE_GAUGE,
    RESERVATIONS_CAPTURED_TOTAL,
    RESERVATIONS_RELEASED_TOTAL,
    TASK_COMPLETIONS_TOTAL,
    TASK_DURATION_SECONDS,
    TASK_FAILURES_TOTAL,
    TASKS_EXECUTED_TOTAL,
)
from solution2.observability.tracing import configure_process_tracing, start_span
from solution2.services.auth import task_state_key
from solution2.utils.logging import configure_logging, get_logger

logger = get_logger("solution2.workers.worker")

try:
    import pika  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - exercised in dependency-injected tests
    pika = None

WORKER_QUEUES = ("queue.realtime", "queue.fast", "queue.batch")
WORKER_CONSUMER_NAME = "worker"
TASK_SUBMITTED_EVENT = "task.submitted"
TASK_COMPLETED_EVENT = "task.completed"
TASK_FAILED_EVENT = "task.failed"
DEFAULT_WORKER_WARMUP_SECONDS = 10.0


@dataclass(frozen=True)
class RabbitMQDelivery:
    """Single RabbitMQ delivery returned by polling."""

    queue_name: str
    routing_key: str
    delivery_tag: int
    message_id: str | None
    body: str


@dataclass(frozen=True)
class TaskExecutionCommand:
    """Parsed task command payload consumed by the worker."""

    event_id: UUID
    task_id: UUID
    user_id: UUID
    x: int
    y: int
    cost: int
    mode: str
    tier: str
    model_class: ModelClass
    queue_name: str
    routing_key: str
    event_type: str
    trace_id: str | None


@dataclass
class WorkerRuntime:
    """Runtime dependencies for worker execution."""

    settings: AppSettings
    db_pool: asyncpg.Pool
    redis_client: Redis[str]
    consumer: RabbitMQTaskConsumer
    model: WorkerModel


class RabbitMQTaskConsumer:
    """Small blocking RabbitMQ consumer wrapper used from asyncio threads."""

    def __init__(
        self,
        *,
        rabbitmq_url: str,
        socket_connect_timeout: float = 3.0,
        heartbeat: int = 60,
        prefetch_count: int = 1,
    ) -> None:
        self._rabbitmq_url = rabbitmq_url
        self._socket_connect_timeout = socket_connect_timeout
        self._heartbeat = heartbeat
        self._prefetch_count = max(1, prefetch_count)
        self._connection: Any | None = None
        self._channel: Any | None = None

    def connect(self) -> None:
        self._require_pika()
        if self._connection is not None and self._channel is not None:
            return
        if pika is None:
            raise RuntimeError("pika is unavailable")
        parameters = pika.URLParameters(self._rabbitmq_url)
        parameters.heartbeat = self._heartbeat
        parameters.blocked_connection_timeout = self._socket_connect_timeout
        parameters.socket_timeout = self._socket_connect_timeout
        connection = pika.BlockingConnection(parameters=parameters)
        channel = connection.channel()
        channel.basic_qos(prefetch_count=self._prefetch_count)
        self._connection = connection
        self._channel = channel

    def ensure_queues(self, *, queue_names: tuple[str, ...]) -> None:
        channel = self._ensure_connected_channel()
        for queue_name in queue_names:
            channel.queue_declare(queue=queue_name, durable=True, passive=True)

    def get_one(self, *, queue_names: tuple[str, ...]) -> RabbitMQDelivery | None:
        channel = self._ensure_connected_channel()
        for queue_name in queue_names:
            method, properties, body = channel.basic_get(queue=queue_name, auto_ack=False)
            if method is None or body is None:
                continue
            raw_body = body.decode("utf-8") if isinstance(body, bytes) else str(body)
            routing_key = str(getattr(method, "routing_key", queue_name))
            delivery_tag = int(method.delivery_tag)
            message_id_value = getattr(properties, "message_id", None)
            message_id = str(message_id_value) if message_id_value else None
            return RabbitMQDelivery(
                queue_name=queue_name,
                routing_key=routing_key,
                delivery_tag=delivery_tag,
                message_id=message_id,
                body=raw_body,
            )
        return None

    def queue_depths(self, *, queue_names: tuple[str, ...]) -> dict[str, int]:
        channel = self._ensure_connected_channel()
        depths: dict[str, int] = {}
        for queue_name in queue_names:
            declared = channel.queue_declare(queue=queue_name, durable=True, passive=True)
            method = getattr(declared, "method", None)
            message_count = int(getattr(method, "message_count", 0))
            depths[queue_name] = max(0, message_count)
        return depths

    def ack(self, *, delivery_tag: int) -> None:
        channel = self._ensure_connected_channel()
        channel.basic_ack(delivery_tag=delivery_tag)

    def nack(self, *, delivery_tag: int, requeue: bool) -> None:
        channel = self._ensure_connected_channel()
        channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)

    def close(self) -> None:
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("rabbitmq_channel_close_failed", error=str(exc))
            self._channel = None
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("rabbitmq_connection_close_failed", error=str(exc))
            self._connection = None

    @staticmethod
    def _require_pika() -> None:
        if pika is None:
            raise RuntimeError("pika is not installed; add `pika` to solution2 dependencies")

    def _ensure_connected_channel(self) -> Any:
        if self._channel is None:
            raise RuntimeError("rabbitmq consumer is not connected")
        return self._channel


class WorkerModel:
    """Simulated model runtime used by worker execution."""

    def __init__(
        self,
        *,
        warmup_seconds: float = DEFAULT_WORKER_WARMUP_SECONDS,
        sleep_fn: Any = asyncio.sleep,
    ) -> None:
        self._warmup_seconds = max(0.0, float(warmup_seconds))
        self._sleep_fn = cast(Any, sleep_fn)
        self._warmed = False
        self._warmup_lock = asyncio.Lock()

    async def warmup(self) -> None:
        if self._warmed:
            return
        async with self._warmup_lock:
            if self._warmup_seconds > 0:
                await self._sleep_fn(self._warmup_seconds)
            self._warmed = True
            logger.info("worker_model_warmed", warmup_seconds=self._warmup_seconds)

    async def execute(self, command: TaskExecutionCommand) -> tuple[dict[str, int], int]:
        await self.warmup()
        runtime_seconds = runtime_seconds_for_model(command.model_class)
        started_at = time.perf_counter()
        await self._sleep_fn(runtime_seconds)
        elapsed_seconds = time.perf_counter() - started_at
        TASK_DURATION_SECONDS.labels(model_class=command.model_class.value).observe(elapsed_seconds)
        runtime_ms = max(1, int(elapsed_seconds * 1000))
        return {"z": command.x + command.y}, runtime_ms


def _idle_sleep_seconds(settings: AppSettings) -> float:
    return max(0.01, float(getattr(settings, "worker_loop_idle_sleep_seconds", 0.05)))


def _error_backoff_seconds(settings: AppSettings) -> float:
    return max(0.05, float(getattr(settings, "worker_error_backoff_seconds", 1.0)))


def _parse_task_command(delivery: RabbitMQDelivery) -> TaskExecutionCommand | None:
    try:
        payload = json.loads(delivery.body)
    except json.JSONDecodeError:
        logger.warning(
            "worker_payload_decode_failed",
            queue=delivery.queue_name,
            delivery_tag=delivery.delivery_tag,
        )
        return None

    if not isinstance(payload, dict):
        logger.warning(
            "worker_payload_invalid_shape",
            queue=delivery.queue_name,
            delivery_tag=delivery.delivery_tag,
        )
        return None

    try:
        event_id_raw = payload.get("event_id") or delivery.message_id
        if not isinstance(event_id_raw, str):
            raise ValueError("missing event_id")
        task_id = UUID(str(payload["task_id"]))
        user_id = UUID(str(payload["user_id"]))
        mode = str(payload.get("mode", "async"))
        tier = str(payload.get("tier", "free"))
        model_class = ModelClass(str(payload.get("model_class", "small")))
        event_type = str(payload.get("event_type", TASK_SUBMITTED_EVENT))
        trace_id_raw = payload.get("trace_id")
        trace_id = str(trace_id_raw) if isinstance(trace_id_raw, str) else None

        if event_type != TASK_SUBMITTED_EVENT:
            # Non-submitted lifecycle events share routing lanes with submitted tasks.
            # Parse only identity/routing fields and let the dispatcher skip cleanly.
            return TaskExecutionCommand(
                event_id=UUID(event_id_raw),
                task_id=task_id,
                user_id=user_id,
                x=0,
                y=0,
                cost=int(payload.get("cost", 0)),
                mode=mode,
                tier=tier,
                model_class=model_class,
                queue_name=delivery.queue_name,
                routing_key=delivery.routing_key,
                event_type=event_type,
                trace_id=trace_id,
            )

        x = int(payload["x"])
        y = int(payload["y"])
        cost = int(payload["cost"])
        return TaskExecutionCommand(
            event_id=UUID(event_id_raw),
            task_id=task_id,
            user_id=user_id,
            x=x,
            y=y,
            cost=cost,
            mode=mode,
            tier=tier,
            model_class=model_class,
            queue_name=delivery.queue_name,
            routing_key=delivery.routing_key,
            event_type=event_type,
            trace_id=trace_id,
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning(
            "worker_payload_invalid",
            error=str(exc),
            queue=delivery.queue_name,
            delivery_tag=delivery.delivery_tag,
        )
        return None


async def _set_running_state(*, runtime: WorkerRuntime, command: TaskExecutionCommand) -> bool:
    existing = await get_task_command(runtime.db_pool, command.task_id)
    if existing is None:
        return False
    if existing.status in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.EXPIRED,
    ):
        return False
    if existing.status == TaskStatus.RUNNING:
        return True
    return await update_task_command_running(runtime.db_pool, task_id=command.task_id)


async def _persist_success(
    *,
    runtime: WorkerRuntime,
    command: TaskExecutionCommand,
    result_payload: dict[str, int],
    runtime_ms: int,
) -> bool:
    async with runtime.db_pool.acquire() as connection, connection.transaction():
        dedup_inserted = await record_inbox_event(
            connection,
            event_id=command.event_id,
            consumer_name=WORKER_CONSUMER_NAME,
        )
        if not dedup_inserted:
            return False

        status_updated = await update_task_command_completed(connection, task_id=command.task_id)
        if not status_updated:
            return False

        reservation = await get_credit_reservation(
            connection,
            task_id=command.task_id,
            for_update=True,
        )
        if reservation is None:
            raise RuntimeError("reservation missing for completed task")
        if reservation.state != ReservationState.RESERVED:
            raise RuntimeError("reservation must be RESERVED before capture")

        captured = await capture_reservation(connection, task_id=command.task_id)
        if not captured:
            raise RuntimeError("reservation capture failed")

        await create_outbox_event(
            connection,
            aggregate_id=command.task_id,
            event_type=TASK_COMPLETED_EVENT,
            routing_key=command.routing_key,
            payload={
                "task_id": str(command.task_id),
                "user_id": str(command.user_id),
                "mode": command.mode,
                "tier": command.tier,
                "model_class": command.model_class.value,
                "queue": command.queue_name,
                "cost": command.cost,
                "status": TaskStatus.COMPLETED.value,
                "result": result_payload,
                "runtime_ms": runtime_ms,
                "trace_id": command.trace_id,
            },
        )
    return True


async def _persist_failure(
    *,
    runtime: WorkerRuntime,
    command: TaskExecutionCommand,
    error_message: str,
) -> bool:
    async with runtime.db_pool.acquire() as connection, connection.transaction():
        dedup_inserted = await record_inbox_event(
            connection,
            event_id=command.event_id,
            consumer_name=WORKER_CONSUMER_NAME,
        )
        if not dedup_inserted:
            return False

        status_updated = await update_task_command_failed(connection, task_id=command.task_id)
        if not status_updated:
            return False

        reservation = await get_credit_reservation(
            connection,
            task_id=command.task_id,
            for_update=True,
        )
        if reservation is None:
            raise RuntimeError("reservation missing for failed task")
        if reservation.state != ReservationState.RESERVED:
            raise RuntimeError("reservation must be RESERVED before release")

        released = await release_reservation(connection, task_id=command.task_id)
        if not released:
            raise RuntimeError("reservation release failed")

        updated_balance = await add_user_credits(
            connection,
            user_id=reservation.user_id,
            delta=reservation.amount,
        )
        if updated_balance is None:
            raise RuntimeError("credit refund target user not found")

        await insert_credit_transaction(
            connection,
            user_id=reservation.user_id,
            task_id=command.task_id,
            delta=reservation.amount,
            reason="task_failed_refund",
        )

        await create_outbox_event(
            connection,
            aggregate_id=command.task_id,
            event_type=TASK_FAILED_EVENT,
            routing_key=command.routing_key,
            payload={
                "task_id": str(command.task_id),
                "user_id": str(command.user_id),
                "mode": command.mode,
                "tier": command.tier,
                "model_class": command.model_class.value,
                "queue": command.queue_name,
                "cost": command.cost,
                "status": TaskStatus.FAILED.value,
                "error": error_message,
                "trace_id": command.trace_id,
            },
        )
    return True


async def _write_terminal_cache(
    *,
    runtime: WorkerRuntime,
    command: TaskExecutionCommand,
    status: TaskStatus,
    result_payload: dict[str, int] | None,
    error_message: str | None,
    runtime_ms: int | None,
) -> None:
    completed_at_epoch = int(time.time())

    task_state_mapping: dict[str | bytes, str | int | float | bytes] = {
        "status": status.value,
        "task_id": str(command.task_id),
        "user_id": str(command.user_id),
        "queue": command.queue_name,
        "cost": str(command.cost),
        "completed_at_epoch": str(completed_at_epoch),
        "error": error_message or "",
    }
    if runtime_ms is not None:
        task_state_mapping["runtime_ms"] = str(runtime_ms)
    if result_payload is not None:
        task_state_mapping["result"] = json.dumps(result_payload, separators=(",", ":"))

    await runtime.redis_client.hset(task_state_key(command.task_id), mapping=task_state_mapping)
    await runtime.redis_client.expire(
        task_state_key(command.task_id),
        runtime.settings.redis_task_state_ttl_seconds,
    )


async def _process_delivery(
    *,
    runtime: WorkerRuntime,
    delivery: RabbitMQDelivery,
) -> TaskCompletionMetricStatus:
    command = _parse_task_command(delivery)
    if command is None:
        return TaskCompletionMetricStatus.SKIPPED

    if command.event_type != TASK_SUBMITTED_EVENT:
        logger.info(
            "worker_event_skipped",
            event_type=command.event_type,
            task_id=str(command.task_id),
        )
        return TaskCompletionMetricStatus.SKIPPED

    with start_span(
        tracer_name="solution2.worker",
        span_name="worker.process_task",
        attributes={
            "task.id": str(command.task_id),
            "task.model_class": command.model_class.value,
            "task.queue": command.queue_name,
        },
    ):
        running = await _set_running_state(runtime=runtime, command=command)
        if not running:
            return TaskCompletionMetricStatus.SKIPPED

        try:
            result_payload, runtime_ms = await runtime.model.execute(command)
        except Exception as exc:
            error_message = f"worker_execution_failed:{exc}"
            TASK_FAILURES_TOTAL.labels(reason="execution").inc()
            applied = await _persist_failure(
                runtime=runtime,
                command=command,
                error_message=error_message,
            )
            if not applied:
                return TaskCompletionMetricStatus.SKIPPED
            RESERVATIONS_RELEASED_TOTAL.inc()
            RESERVATIONS_ACTIVE_GAUGE.dec()
            await _write_terminal_cache(
                runtime=runtime,
                command=command,
                status=TaskStatus.FAILED,
                result_payload=None,
                error_message=error_message,
                runtime_ms=None,
            )
            TASK_COMPLETIONS_TOTAL.labels(status=TaskStatus.FAILED.value).inc()
            return TaskCompletionMetricStatus.FAILED

        applied = await _persist_success(
            runtime=runtime,
            command=command,
            result_payload=result_payload,
            runtime_ms=runtime_ms,
        )
        if not applied:
            return TaskCompletionMetricStatus.SKIPPED

        RESERVATIONS_CAPTURED_TOTAL.inc()
        RESERVATIONS_ACTIVE_GAUGE.dec()
        await _write_terminal_cache(
            runtime=runtime,
            command=command,
            status=TaskStatus.COMPLETED,
            result_payload=result_payload,
            error_message=None,
            runtime_ms=runtime_ms,
        )
        TASK_COMPLETIONS_TOTAL.labels(status=TaskStatus.COMPLETED.value).inc()
        return TaskCompletionMetricStatus.COMPLETED


async def _refresh_worker_heartbeat(runtime: WorkerRuntime) -> None:
    await runtime.redis_client.set(
        runtime.settings.worker_heartbeat_key,
        str(int(time.time())),
        ex=runtime.settings.worker_heartbeat_ttl_seconds,
    )


async def _refresh_queue_depth_metrics(runtime: WorkerRuntime) -> None:
    try:
        queue_depths = await asyncio.to_thread(
            runtime.consumer.queue_depths,
            queue_names=WORKER_QUEUES,
        )
        for queue_name, depth in queue_depths.items():
            RABBITMQ_QUEUE_DEPTH.labels(queue=queue_name).set(depth)
    except Exception as exc:
        logger.warning("worker_queue_depth_refresh_failed", error=str(exc))


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
        service_name=f"{base_service_name}-worker",
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
    model = WorkerModel()

    runtime = WorkerRuntime(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        consumer=consumer,
        model=model,
    )

    try:
        await redis_client.ping()
        await asyncio.to_thread(consumer.connect)
        await asyncio.to_thread(consumer.ensure_queues, queue_names=WORKER_QUEUES)
        await asyncio.wait_for(
            model.warmup(),
            timeout=max(1.0, settings.worker_loop_bootstrap_timeout_seconds),
        )
    except Exception as exc:
        logger.exception("worker_startup_failed", error=str(exc))
        consumer.close()
        await redis_client.close()
        await db_pool.close()
        return

    try:
        start_http_server(settings.worker_metrics_port)
    except OSError as exc:
        logger.warning(
            "worker_metrics_port_in_use",
            port=settings.worker_metrics_port,
            error=str(exc),
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("worker_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        while not stop_event.is_set():
            await _refresh_worker_heartbeat(runtime)
            await _refresh_queue_depth_metrics(runtime)
            delivery = await asyncio.to_thread(runtime.consumer.get_one, queue_names=WORKER_QUEUES)
            if delivery is None:
                await asyncio.sleep(_idle_sleep_seconds(settings))
                continue

            try:
                status = await asyncio.wait_for(
                    _process_delivery(runtime=runtime, delivery=delivery),
                    timeout=max(1.0, settings.worker_loop_task_timeout_seconds),
                )
                TASKS_EXECUTED_TOTAL.labels(
                    status=status.value,
                    queue=delivery.queue_name,
                ).inc()
                await asyncio.to_thread(runtime.consumer.ack, delivery_tag=delivery.delivery_tag)
            except TimeoutError:
                TASK_FAILURES_TOTAL.labels(reason="timeout").inc()
                logger.warning(
                    "worker_task_timeout",
                    queue=delivery.queue_name,
                    delivery_tag=delivery.delivery_tag,
                )
                await asyncio.to_thread(
                    runtime.consumer.nack,
                    delivery_tag=delivery.delivery_tag,
                    requeue=True,
                )
            except Exception as exc:
                TASK_FAILURES_TOTAL.labels(reason="processing").inc()
                logger.exception(
                    "worker_delivery_failed",
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
        logger.info("worker_shutdown_complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
