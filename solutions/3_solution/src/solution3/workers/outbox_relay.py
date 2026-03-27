from __future__ import annotations

import argparse
import asyncio
import signal
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

import asyncpg
from prometheus_client import start_http_server

from solution3.core.settings import load_settings
from solution3.db.repository import fetch_unpublished_outbox_events, mark_outbox_events_published
from solution3.observability.metrics import (
    OUTBOX_EVENTS_PUBLISHED_TOTAL,
    OUTBOX_PUBLISH_LAG_SECONDS,
)
from solution3.utils.logging import configure_logging, get_logger

try:
    from kafka import KafkaProducer
except ImportError:  # pragma: no cover - explicit runtime guard tested via monkeypatch
    KafkaProducer = None

logger = get_logger("solution3.workers.outbox_relay")


class RelayProducer(Protocol):
    def produce(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: Mapping[str, str],
    ) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class DeliveryFuture(Protocol):
    def get(self, timeout: float | None = None) -> object: ...


class KafkaProducerClient(Protocol):
    def send(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        headers: list[tuple[str, bytes]],
    ) -> DeliveryFuture: ...

    def flush(self, timeout: float | None = None) -> None: ...

    def close(self, timeout: float | None = None) -> None: ...


class RedpandaSettings(Protocol):
    redpanda_bootstrap_servers: str
    outbox_relay_metrics_port: int


def _event_lag_seconds(created_at: datetime) -> float:
    now = datetime.now(tz=UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return max((now - created_at).total_seconds(), 0.0)


class RedpandaRelayProducer:
    def __init__(
        self,
        *,
        producer: KafkaProducerClient,
        flush_timeout_seconds: float = 10.0,
        delivery_timeout_seconds: float = 10.0,
    ) -> None:
        self._producer = producer
        self._flush_timeout_seconds = flush_timeout_seconds
        self._delivery_timeout_seconds = delivery_timeout_seconds
        self._pending_futures: list[DeliveryFuture] = []

    def produce(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: Mapping[str, str],
    ) -> None:
        future = self._producer.send(
            topic,
            key=key,
            value=value,
            headers=[
                (header_key, header_value.encode("utf-8"))
                for header_key, header_value in headers.items()
            ],
        )
        self._pending_futures.append(future)

    def flush(self) -> None:
        pending_futures = list(self._pending_futures)
        self._pending_futures.clear()
        self._producer.flush(timeout=self._flush_timeout_seconds)
        for future in pending_futures:
            future.get(timeout=self._delivery_timeout_seconds)

    def close(self) -> None:
        self._producer.close(timeout=self._flush_timeout_seconds)


def build_redpanda_producer(settings: RedpandaSettings) -> RedpandaRelayProducer:
    if KafkaProducer is None:
        raise RuntimeError("kafka-python is not installed")

    bootstrap_servers = [
        server.strip()
        for server in settings.redpanda_bootstrap_servers.split(",")
        if server.strip()
    ]
    if not bootstrap_servers:
        raise RuntimeError("redpanda bootstrap servers are not configured")

    producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        acks="all",
        client_id="solution3-outbox-relay",
        linger_ms=10,
    )
    return RedpandaRelayProducer(producer=producer)


async def relay_once(
    *,
    db_pool: asyncpg.Pool,
    producer: RelayProducer,
    batch_size: int = 100,
) -> int:
    events = await fetch_unpublished_outbox_events(db_pool, limit=batch_size)
    if not events:
        OUTBOX_PUBLISH_LAG_SECONDS.set(0.0)
        return 0

    OUTBOX_PUBLISH_LAG_SECONDS.set(_event_lag_seconds(events[0].created_at))
    for event in events:
        producer.produce(
            topic=event.topic,
            key=str(event.event_id).encode("utf-8"),
            value=event.payload.encode("utf-8"),
            headers={"event_id": str(event.event_id)},
        )

    producer.flush()
    await mark_outbox_events_published(db_pool, event_ids=[event.event_id for event in events])
    for event in events:
        OUTBOX_EVENTS_PUBLISHED_TOTAL.labels(topic=event.topic, result="ok").inc()
    return len(events)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 outbox relay")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_args: stop_event.set())


async def _main_async(*, interval_seconds: float, batch_size: int) -> None:
    settings = load_settings()
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    start_http_server(settings.outbox_relay_metrics_port)
    producer = build_redpanda_producer(settings)
    stop_event = asyncio.Event()
    _install_stop_handlers(stop_event)

    logger.info(
        "outbox_relay_started",
        interval_seconds=interval_seconds,
        batch_size=batch_size,
    )
    try:
        while not stop_event.is_set():
            try:
                published = await relay_once(
                    db_pool=db_pool,
                    producer=producer,
                    batch_size=batch_size,
                )
                if published > 0:
                    logger.info("outbox_relay_batch_published", count=published)
                    continue
            except Exception as exc:
                logger.exception("outbox_relay_iteration_failed", error=str(exc))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        producer.close()
        await db_pool.close()
        logger.info("outbox_relay_stopped")


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    asyncio.run(
        _main_async(
            interval_seconds=max(float(args.interval), 0.1),
            batch_size=max(int(args.batch_size), 1),
        )
    )


if __name__ == "__main__":
    main()
