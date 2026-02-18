"""Reservation watchdog for Solution 2 command/query consistency."""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from prometheus_client import start_http_server
from redis.asyncio import Redis

from solution2.constants import TaskStatus, compute_routing_key, resolve_queue
from solution2.core.settings import AppSettings, load_settings
from solution2.db.migrate import run_migrations
from solution2.db.repository import (
    add_user_credits,
    count_total_active_reservations,
    create_outbox_event,
    find_expired_reservations,
    get_task_command,
    insert_credit_transaction,
    release_reservation,
    update_task_command_timed_out,
)
from solution2.observability.metrics import (
    CREDITS_RELEASED_TOTAL,
    REDIS_KEYS_CLEANED_TOTAL,
    RESERVATIONS_ACTIVE_GAUGE,
    RESERVATIONS_EXPIRED_TOTAL,
    RESERVATIONS_RELEASED_TOTAL,
)
from solution2.observability.tracing import configure_process_tracing, start_span
from solution2.services.auth import task_state_key
from solution2.utils.logging import configure_logging, get_logger

logger = get_logger("solution2.workers.watchdog")

TASK_TIMED_OUT_EVENT = "task.timed_out"
TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.TIMEOUT.value,
        TaskStatus.EXPIRED.value,
    }
)


@dataclass
class WatchdogRuntime:
    """Runtime dependencies for watchdog cycles."""

    settings: AppSettings
    db_pool: asyncpg.Pool
    redis_client: Redis[str]


def _watchdog_interval_seconds(settings: AppSettings) -> float:
    return max(0.1, float(settings.watchdog_interval_seconds))


def _watchdog_error_backoff_seconds(settings: AppSettings) -> float:
    return max(0.1, float(settings.watchdog_error_backoff_seconds))


def _watchdog_scan_count(settings: AppSettings) -> int:
    return max(1, int(settings.watchdog_scan_count))


def _metrics_port(settings: AppSettings) -> int:
    return int(settings.watchdog_metrics_port)


def _task_id_from_task_key(task_key: str) -> UUID | None:
    if not task_key.startswith("task:"):
        return None
    raw_task_id = task_key.split(":", 1)[1]
    try:
        return UUID(raw_task_id)
    except ValueError:
        return None


def _coerce_epoch(raw_value: object) -> int | None:
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str) and raw_value.isdigit():
        return int(raw_value)
    return None


async def _write_timeout_cache(
    *,
    runtime: WatchdogRuntime,
    task_id: UUID,
    user_id: UUID,
    queue_name: str | None,
) -> None:
    completed_at_epoch = int(time.time())
    task_mapping: dict[str | bytes, str | int | float | bytes] = {
        "task_id": str(task_id),
        "user_id": str(user_id),
        "status": TaskStatus.TIMEOUT.value,
        "queue": queue_name or "",
        "error": "Task timed out by watchdog",
        "completed_at_epoch": completed_at_epoch,
    }
    task_key = task_state_key(task_id)
    await runtime.redis_client.hset(task_key, mapping=task_mapping)
    await runtime.redis_client.expire(task_key, runtime.settings.redis_task_state_ttl_seconds)


async def _expire_reservation(
    *,
    runtime: WatchdogRuntime,
    task_id: UUID,
    user_id: UUID,
    amount: int,
) -> tuple[bool, str | None]:
    async with runtime.db_pool.acquire() as connection, connection.transaction():
        command = await get_task_command(connection, task_id)
        if command is None:
            return False, None

        released = await release_reservation(connection, task_id=task_id)
        if not released:
            return False, None

        updated_balance = await add_user_credits(
            connection,
            user_id=user_id,
            delta=amount,
        )
        if updated_balance is None:
            raise RuntimeError("credit refund target user not found")

        timed_out = await update_task_command_timed_out(connection, task_id=task_id)
        if not timed_out:
            raise RuntimeError("failed to transition task command to TIMEOUT")

        await insert_credit_transaction(
            connection,
            user_id=user_id,
            task_id=task_id,
            delta=amount,
            reason="reservation_timeout_refund",
        )

        queue_name = resolve_queue(
            tier=command.tier,
            mode=command.mode,
            model_class=command.model_class,
        )
        routing_base = compute_routing_key(
            mode=command.mode,
            tier=command.tier,
            model_class=command.model_class,
        )
        routing_key = f"{routing_base}.timed_out"
        await create_outbox_event(
            connection,
            aggregate_id=task_id,
            event_type=TASK_TIMED_OUT_EVENT,
            routing_key=routing_key,
            payload={
                "task_id": str(task_id),
                "user_id": str(user_id),
                "mode": command.mode.value,
                "tier": command.tier.value,
                "model_class": command.model_class.value,
                "queue": queue_name,
                "cost": amount,
                "status": TaskStatus.TIMEOUT.value,
                "error": "Task timed out by watchdog",
            },
        )
        return True, queue_name


async def _process_expired_reservations(runtime: WatchdogRuntime) -> tuple[int, int]:
    as_of = datetime.now(tz=UTC)
    expired_reservations = await find_expired_reservations(runtime.db_pool, as_of=as_of)
    expired_count = 0
    released_credits = 0

    for reservation in expired_reservations:
        with start_span(
            tracer_name="solution2.watchdog",
            span_name="watchdog.expire_reservation",
            attributes={
                "task.id": str(reservation.task_id),
                "reservation.id": str(reservation.reservation_id),
            },
        ):
            released, queue_name = await _expire_reservation(
                runtime=runtime,
                task_id=reservation.task_id,
                user_id=reservation.user_id,
                amount=reservation.amount,
            )
            if not released:
                continue

            await _write_timeout_cache(
                runtime=runtime,
                task_id=reservation.task_id,
                user_id=reservation.user_id,
                queue_name=queue_name,
            )
            expired_count += 1
            released_credits += reservation.amount

    return expired_count, released_credits


async def _cleanup_terminal_redis(runtime: WatchdogRuntime) -> int:
    now_epoch = int(time.time())
    ttl_seconds = runtime.settings.task_result_ttl_seconds
    cleaned = 0
    async for key in runtime.redis_client.scan_iter(
        match="task:*",
        count=_watchdog_scan_count(runtime.settings),
    ):
        task_state = await runtime.redis_client.hgetall(key)
        if not task_state:
            continue

        status = str(task_state.get("status", ""))
        if status not in TERMINAL_STATUSES:
            continue
        completed_at = _coerce_epoch(task_state.get("completed_at_epoch"))
        if completed_at is None or (now_epoch - completed_at) < ttl_seconds:
            continue

        task_id = _task_id_from_task_key(str(key))
        if task_id is None:
            continue
        _ = task_id
        cleaned += int(await runtime.redis_client.delete(str(key)))
    return cleaned


async def _refresh_reservation_metrics(runtime: WatchdogRuntime) -> int:
    active_reservations = await count_total_active_reservations(runtime.db_pool)
    RESERVATIONS_ACTIVE_GAUGE.set(float(active_reservations))
    return active_reservations


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
        service_name=f"{base_service_name}-watchdog",
    )

    await run_migrations(str(settings.postgres_dsn))
    db_pool = await _build_db_pool(settings)
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    runtime = WatchdogRuntime(settings=settings, db_pool=db_pool, redis_client=redis_client)

    try:
        await redis_client.ping()
    except Exception as exc:
        logger.exception("watchdog_startup_failed", error=str(exc))
        await redis_client.close()
        await db_pool.close()
        return

    try:
        start_http_server(_metrics_port(settings))
    except OSError as exc:
        logger.warning("watchdog_metrics_port_in_use", port=_metrics_port(settings), error=str(exc))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("watchdog_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        while not stop_event.is_set():
            try:
                expired_count, released_credits = await _process_expired_reservations(runtime)
                cleaned_keys = await _cleanup_terminal_redis(runtime)
                active_reservations = await _refresh_reservation_metrics(runtime)
                if expired_count:
                    RESERVATIONS_EXPIRED_TOTAL.inc(expired_count)
                    RESERVATIONS_RELEASED_TOTAL.inc(expired_count)
                if released_credits:
                    CREDITS_RELEASED_TOTAL.inc(released_credits)
                if cleaned_keys:
                    REDIS_KEYS_CLEANED_TOTAL.inc(cleaned_keys)
                logger.info(
                    "watchdog_cycle_completed",
                    expired_reservations=expired_count,
                    released_credits=released_credits,
                    redis_keys_cleaned=cleaned_keys,
                    active_reservations=active_reservations,
                )
            except Exception as exc:
                logger.exception("watchdog_cycle_failed", error=str(exc))
                await asyncio.sleep(_watchdog_error_backoff_seconds(settings))
                continue
            await asyncio.sleep(_watchdog_interval_seconds(settings))
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await redis_client.close()
        await db_pool.close()
        logger.info("watchdog_shutdown_complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
