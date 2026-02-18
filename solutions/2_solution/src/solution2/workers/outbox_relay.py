"""Outbox relay worker: stream durable commands from DB to RabbitMQ."""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
from prometheus_client import start_http_server

from solution2.core.settings import AppSettings, load_settings
from solution2.db.migrate import run_migrations
from solution2.db.repository import (
    list_unpublished_outbox_events,
    mark_outbox_event_published,
    purge_old_outbox_events,
)
from solution2.observability.metrics import OUTBOX_PUBLISH_LAG_SECONDS
from solution2.observability.tracing import configure_process_tracing, start_span
from solution2.services.rabbitmq import RabbitMQRelay
from solution2.utils.logging import configure_logging, get_logger

logger = get_logger("solution2.workers.outbox_relay")


@dataclass
class OutboxRelayRuntime:
    settings: AppSettings
    db_pool: asyncpg.Pool
    rabbitmq: RabbitMQRelay


def _batch_size(settings: AppSettings) -> int:
    return max(1, int(getattr(settings, "outbox_relay_batch_size", 100)))


def _empty_cycle_sleep_seconds(settings: AppSettings) -> float:
    return max(0.01, float(getattr(settings, "outbox_relay_empty_backoff_seconds", 0.05)))


def _error_cycle_sleep_seconds(settings: AppSettings) -> float:
    return max(0.05, float(getattr(settings, "outbox_relay_error_backoff_seconds", 1.0)))


def _purge_interval_seconds(settings: AppSettings) -> float:
    return max(1.0, float(getattr(settings, "outbox_relay_purge_interval_seconds", 60.0)))


def _purge_retention_seconds(settings: AppSettings) -> int:
    return max(1, int(getattr(settings, "outbox_relay_purge_retention_seconds", 604_800)))


def _purge_batch_size(settings: AppSettings) -> int:
    return max(1, int(getattr(settings, "outbox_relay_purge_batch_size", 500)))


def _metrics_port(settings: AppSettings) -> int:
    return int(getattr(settings, "outbox_relay_metrics_port", 9200))


def _event_lag_seconds(event_created_at: datetime) -> float:
    return max(0.0, (datetime.now(tz=UTC) - event_created_at).total_seconds())


async def _build_db_pool(settings: AppSettings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=str(settings.postgres_dsn),
        min_size=settings.db_pool_min_size,
        max_size=max(2, min(settings.db_pool_max_size, 8)),
        command_timeout=settings.db_pool_command_timeout_seconds,
        server_settings={
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "idle_in_transaction_session_timeout": str(settings.db_idle_in_transaction_timeout_ms),
        },
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime_seconds,
    )


async def _publish_batch(*, runtime: OutboxRelayRuntime) -> int:
    """Publish one batch of pending outbox events preserving DB ordering."""
    outbox_events = await list_unpublished_outbox_events(
        runtime.db_pool, limit=_batch_size(runtime.settings)
    )
    if not outbox_events:
        OUTBOX_PUBLISH_LAG_SECONDS.set(0.0)
        return 0

    oldest_created_at = outbox_events[0].created_at
    OUTBOX_PUBLISH_LAG_SECONDS.set(_event_lag_seconds(oldest_created_at))

    published = 0
    for event in outbox_events:
        payload = dict(event.payload)
        payload["event_id"] = str(event.event_id)
        payload.setdefault("aggregate_id", str(event.aggregate_id))
        payload.setdefault("event_type", event.event_type)

        try:
            with start_span(
                tracer_name="solution2.outbox_relay",
                span_name="outbox.relay.publish",
                attributes={
                    "outbox.event_id": str(event.event_id),
                    "outbox.routing_key": event.routing_key,
                },
            ):
                runtime.rabbitmq.publish(
                    event_id=str(event.event_id),
                    routing_key=event.routing_key,
                    payload=payload,
                )
        except Exception as exc:
            logger.exception(
                "outbox_publish_failed",
                event_id=str(event.event_id),
                routing_key=event.routing_key,
                error=str(exc),
            )
            return published

        try:
            marked = await mark_outbox_event_published(runtime.db_pool, event_id=event.event_id)
        except Exception as exc:
            logger.exception(
                "outbox_mark_published_failed",
                event_id=str(event.event_id),
                error=str(exc),
            )
            return published

        if marked:
            published += 1
        else:
            logger.info("outbox_event_already_published", event_id=str(event.event_id))

    return published


async def _purge_published_events(*, runtime: OutboxRelayRuntime) -> int:
    try:
        return await purge_old_outbox_events(
            runtime.db_pool,
            older_than_seconds=_purge_retention_seconds(runtime.settings),
            batch_size=_purge_batch_size(runtime.settings),
        )
    except Exception as exc:
        logger.warning("outbox_purge_failed", error=str(exc))
        return 0


async def main_async() -> None:
    """Run the outbox relay loop."""
    configure_logging()
    settings = load_settings()
    base_service_name = str(getattr(settings, "app_name", "mc-solution2"))
    configure_process_tracing(
        settings=settings,
        service_name=f"{base_service_name}-outbox-relay",
    )

    await run_migrations(str(settings.postgres_dsn))
    db_pool = await _build_db_pool(settings)
    rabbitmq = RabbitMQRelay(
        rabbitmq_url=settings.rabbitmq_url,
        socket_connect_timeout=float(
            getattr(settings, "outbox_relay_connect_timeout_seconds", 3.0)
        ),
    )
    runtime = OutboxRelayRuntime(settings=settings, db_pool=db_pool, rabbitmq=rabbitmq)

    try:
        rabbitmq.connect()
        rabbitmq.ensure_topology()
    except Exception as exc:
        logger.exception("outbox_relay_startup_failed", error=str(exc))
        await db_pool.close()
        return

    try:
        start_http_server(_metrics_port(settings))
    except OSError as exc:
        logger.warning(
            "outbox_relay_metrics_bind_failed",
            error=str(exc),
            port=_metrics_port(settings),
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("outbox_relay_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    last_purge = time.monotonic()
    try:
        while not stop_event.is_set():
            try:
                published_count = await _publish_batch(runtime=runtime)
                now_monotonic = time.monotonic()
                if now_monotonic - last_purge >= _purge_interval_seconds(settings):
                    purged = await _purge_published_events(runtime=runtime)
                    if purged:
                        logger.info("outbox_purge_completed", purged=purged)
                    last_purge = now_monotonic

                if published_count == 0:
                    await asyncio.sleep(_empty_cycle_sleep_seconds(settings))
                else:
                    await asyncio.sleep(0)
            except Exception as exc:
                logger.exception("outbox_relay_iteration_failed", error=str(exc))
                await asyncio.sleep(_error_cycle_sleep_seconds(settings))
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        rabbitmq.close()
        await db_pool.close()
        logger.info("outbox_relay_shutdown_complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
