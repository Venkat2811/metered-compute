from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

import asyncpg
import pika
import tigerbeetle as tb
from redis.asyncio import Redis

from solution3.constants import (
    BillingState,
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
)
from solution3.core.settings import load_settings
from solution3.db.repository import finalize_task_command, update_task_running
from solution3.models.domain import TaskCommand
from solution3.services.billing import TigerBeetleBilling, resolve_tigerbeetle_addresses
from solution3.utils.logging import configure_logging, get_logger

logger = get_logger("solution3.workers.worker")


class WorkerRedis(Protocol):
    async def decr(self, key: str) -> int: ...

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None: ...

    async def expire(self, key: str, seconds: int) -> None: ...


class WorkerBilling(Protocol):
    def post_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool: ...

    def void_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool: ...


class WarmRegistry(Protocol):
    async def sadd(self, key: str, value: str) -> int: ...


class WorkerQueueMethod(Protocol):
    delivery_tag: int


class WorkerQueueChannel(Protocol):
    def basic_ack(self, *, delivery_tag: int) -> None: ...

    def basic_nack(self, *, delivery_tag: int, requeue: bool) -> None: ...

    def basic_qos(self, *, prefetch_count: int) -> None: ...

    def basic_consume(self, *, queue: str, on_message_callback: object) -> str: ...

    def start_consuming(self) -> None: ...

    def stop_consuming(self) -> None: ...


class WorkerQueueConnection(Protocol):
    def channel(self) -> WorkerQueueChannel: ...

    def close(self) -> None: ...


class WorkerModelRuntime:
    def __init__(
        self,
        *,
        worker_id: str,
        redis_client: WarmRegistry | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.redis_client = redis_client
        self.warm_model: ModelClass | None = None

    async def go_warm(self, model_class: ModelClass) -> None:
        self.warm_model = model_class
        if self.redis_client is not None:
            await self.redis_client.sadd(_warm_registry_key(model_class), self.worker_id)

    async def execute(self, task: TaskCommand) -> dict[str, int]:
        if self.warm_model != task.model_class:
            await asyncio.sleep(3.0)
            await self.go_warm(task.model_class)
        await asyncio.sleep(_inference_seconds(task.model_class))
        return {"sum": task.x + task.y}


def _active_counter_key(user_id: UUID) -> str:
    return f"active:{user_id}"


def _task_state_key(task_id: UUID) -> str:
    return f"task:{task_id}"


def _warm_registry_key(model_class: ModelClass) -> str:
    return f"warm:{model_class.value}"


def _inference_seconds(model_class: ModelClass) -> float:
    if model_class == ModelClass.SMALL:
        return 2.0
    if model_class == ModelClass.MEDIUM:
        return 4.0
    return 6.0


def _require_str(event: Mapping[str, object], key: str) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _require_int(event: Mapping[str, object], key: str) -> int:
    value = event.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def task_command_from_event(event: Mapping[str, object]) -> TaskCommand:
    now = datetime.now(tz=UTC)
    return TaskCommand(
        task_id=UUID(_require_str(event, "task_id")),
        user_id=UUID(_require_str(event, "user_id")),
        tier=SubscriptionTier(_require_str(event, "tier")),
        mode=RequestMode(_require_str(event, "mode")),
        model_class=ModelClass(_require_str(event, "model_class")),
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
        x=_require_int(event, "x"),
        y=_require_int(event, "y"),
        cost=_require_int(event, "cost"),
        tb_pending_transfer_id=UUID(_require_str(event, "tb_pending_transfer_id")),
        callback_url=None,
        idempotency_key=None,
        created_at=now,
        updated_at=now,
    )


async def mark_task_running(
    *,
    db_pool: asyncpg.Pool,
    redis_client: WorkerRedis | None,
    task: TaskCommand,
    result_ttl_seconds: int,
) -> bool:
    updated = await update_task_running(db_pool, task_id=task.task_id)
    if not updated:
        return False
    if redis_client is not None:
        await redis_client.hset(
            _task_state_key(task.task_id),
            mapping={
                "user_id": str(task.user_id),
                "status": TaskStatus.RUNNING.value,
                "billing_state": task.billing_state.value,
                "model_class": task.model_class.value,
            },
        )
        await redis_client.expire(_task_state_key(task.task_id), result_ttl_seconds)
    return True


async def handle_task_completion(
    *,
    db_pool: asyncpg.Pool,
    redis_client: WorkerRedis | None,
    billing: WorkerBilling,
    task: TaskCommand,
    success: bool,
    result_ttl_seconds: int,
    result: dict[str, int] | None = None,
    error: str | None = None,
) -> bool:
    if success:
        tb_ok = billing.post_pending_transfer(pending_transfer_id=task.tb_pending_transfer_id)
        status = TaskStatus.COMPLETED
        billing_state = BillingState.CAPTURED
    else:
        tb_ok = billing.void_pending_transfer(pending_transfer_id=task.tb_pending_transfer_id)
        status = TaskStatus.FAILED
        billing_state = BillingState.RELEASED

    if not tb_ok:
        return False

    finalized = await finalize_task_command(
        db_pool,
        task_id=task.task_id,
        user_id=task.user_id,
        status=status,
        billing_state=billing_state,
        cost=task.cost,
        result=result,
        error=error,
    )
    if not finalized:
        return False

    if redis_client is not None:
        await redis_client.decr(_active_counter_key(task.user_id))
        mapping = {
            "user_id": str(task.user_id),
            "status": status.value,
            "billing_state": billing_state.value,
            "model_class": task.model_class.value,
        }
        if result is not None:
            mapping["result"] = str(result)
        if error is not None:
            mapping["error"] = error
        await redis_client.hset(_task_state_key(task.task_id), mapping=mapping)
        await redis_client.expire(_task_state_key(task.task_id), result_ttl_seconds)
    return True


async def process_task_event(
    *,
    db_pool: asyncpg.Pool,
    redis_client: WorkerRedis | None,
    billing: WorkerBilling,
    runtime: WorkerModelRuntime,
    task: TaskCommand,
    result_ttl_seconds: int,
) -> bool:
    started = await mark_task_running(
        db_pool=db_pool,
        redis_client=redis_client,
        task=task,
        result_ttl_seconds=result_ttl_seconds,
    )
    if not started:
        return True

    try:
        result = await runtime.execute(task)
    except Exception as exc:
        return await handle_task_completion(
            db_pool=db_pool,
            redis_client=redis_client,
            billing=billing,
            task=task,
            success=False,
            result_ttl_seconds=result_ttl_seconds,
            error=str(exc),
        )

    return await handle_task_completion(
        db_pool=db_pool,
        redis_client=redis_client,
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=result_ttl_seconds,
        result=result,
    )


def handle_delivery(
    *,
    channel: WorkerQueueChannel,
    method: WorkerQueueMethod,
    body: bytes,
    db_pool: asyncpg.Pool,
    redis_client: WorkerRedis | None,
    billing: WorkerBilling,
    runtime: WorkerModelRuntime,
    result_ttl_seconds: int,
    run_async: Callable[[Awaitable[bool]], bool] | None = None,
) -> None:
    try:
        decoded = json.loads(body.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("worker payload must decode to an object")
        task = task_command_from_event(decoded)
        async_runner = run_async or asyncio.run
        processed = async_runner(
            process_task_event(
                db_pool=db_pool,
                redis_client=redis_client,
                billing=billing,
                runtime=runtime,
                task=task,
                result_ttl_seconds=result_ttl_seconds,
            )
        )
    except ValueError as exc:
        logger.warning("worker_payload_invalid", error=str(exc))
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    except Exception as exc:
        logger.exception("worker_delivery_failed", error=str(exc))
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return

    if processed:
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return
    channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def build_rabbitmq_channel(rabbitmq_url: str) -> tuple[WorkerQueueConnection, WorkerQueueChannel]:
    parameters = pika.URLParameters(rabbitmq_url)
    parameters.heartbeat = 60
    parameters.blocked_connection_timeout = 3.0
    parameters.socket_timeout = 3.0
    connection = pika.BlockingConnection(parameters=parameters)
    channel = connection.channel()
    return connection, channel


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 worker")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--result-ttl-seconds", type=int, default=86_400)
    return parser.parse_args()


def _build_billing(settings: Any) -> TigerBeetleBilling:
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


def _main_loop(*, interval_seconds: float, result_ttl_seconds: int) -> None:
    settings = load_settings()
    stop_requested = False
    channel: WorkerQueueChannel | None = None

    def _stop(*_args: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        if channel is not None:
            try:
                channel.stop_consuming()
            except Exception:
                logger.exception("worker_stop_consuming_failed")

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    while not stop_requested:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        db_pool: asyncpg.Pool | None = None
        redis_client: Redis[str] | None = None
        connection: WorkerQueueConnection | None = None
        channel = None

        try:

            async def _open_runtime_resources() -> tuple[asyncpg.Pool, Redis[str]]:
                db_pool_inner = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
                redis_client_inner = Redis.from_url(str(settings.redis_url), decode_responses=True)
                await redis_client_inner.ping()
                return db_pool_inner, redis_client_inner

            db_pool, redis_client = event_loop.run_until_complete(_open_runtime_resources())
            worker_redis = cast(WorkerRedis, redis_client)
            billing = _build_billing(settings)
            runtime = WorkerModelRuntime(worker_id="solution3-worker", redis_client=redis_client)
            connection, channel = build_rabbitmq_channel(settings.rabbitmq_url)

            def _on_message(
                ch: WorkerQueueChannel,
                method: WorkerQueueMethod,
                _properties: object,
                body: bytes,
                _event_loop: asyncio.AbstractEventLoop = event_loop,
                _db_pool: asyncpg.Pool = db_pool,
                _redis_client: WorkerRedis = worker_redis,
                _billing: TigerBeetleBilling = billing,
                _runtime: WorkerModelRuntime = runtime,
            ) -> None:
                handle_delivery(
                    channel=ch,
                    method=method,
                    body=body,
                    db_pool=_db_pool,
                    redis_client=_redis_client,
                    billing=_billing,
                    runtime=_runtime,
                    result_ttl_seconds=result_ttl_seconds,
                    run_async=_event_loop.run_until_complete,
                )

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=settings.rabbitmq_queue_cold,
                on_message_callback=_on_message,
            )
            logger.info(
                "worker_started",
                queue=settings.rabbitmq_queue_cold,
                result_ttl_seconds=result_ttl_seconds,
            )
            channel.start_consuming()
        except Exception as exc:
            logger.exception("worker_loop_failed", error=str(exc))
        finally:
            if connection is not None:
                connection.close()
            if db_pool is not None and redis_client is not None:
                close_redis_client = redis_client
                close_db_pool = db_pool

                async def _close_runtime_resources(
                    _redis_client: Redis[str] = close_redis_client,
                    _db_pool: asyncpg.Pool = close_db_pool,
                ) -> None:
                    await _redis_client.close()
                    await _db_pool.close()

                event_loop.run_until_complete(_close_runtime_resources())
            asyncio.set_event_loop(None)
            event_loop.close()
            logger.info("worker_stopped")
            channel = None

        if not stop_requested:
            time.sleep(interval_seconds)


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    _main_loop(
        interval_seconds=max(float(args.interval), 0.1),
        result_ttl_seconds=max(int(args.result_ttl_seconds), 1),
    )


if __name__ == "__main__":
    main()
