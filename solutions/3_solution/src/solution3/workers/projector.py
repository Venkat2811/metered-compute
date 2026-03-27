from __future__ import annotations

import argparse
import asyncio
import json
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast
from uuid import UUID

import asyncpg
from prometheus_client import start_http_server
from redis.asyncio import Redis

from solution3.constants import (
    REDPANDA_TOPIC_TASK_CANCELLED,
    REDPANDA_TOPIC_TASK_COMPLETED,
    REDPANDA_TOPIC_TASK_EXPIRED,
    REDPANDA_TOPIC_TASK_FAILED,
    REDPANDA_TOPIC_TASK_REQUESTED,
    REDPANDA_TOPIC_TASK_STARTED,
)
from solution3.core.settings import load_settings
from solution3.db.repository import apply_task_projection, is_inbox_event_processed
from solution3.models.domain import TaskQueryView
from solution3.observability.metrics import EVENTS_PROJECTED_TOTAL
from solution3.utils.logging import configure_logging, get_logger

try:
    from kafka import KafkaConsumer
except ImportError:  # pragma: no cover - explicit runtime guard tested via monkeypatch
    KafkaConsumer = None

logger = get_logger("solution3.workers.projector")


class ProjectorRedis(Protocol):
    async def hset(self, key: str, mapping: Mapping[str, str]) -> int: ...

    async def expire(self, key: str, seconds: int) -> bool: ...


class ProjectorMessage(Protocol):
    topic: str
    partition: int
    offset: int
    value: bytes
    headers: Sequence[tuple[str, bytes | str | None]]


class ProjectorConsumer(Protocol):
    def poll(
        self, *, timeout_ms: int, max_records: int
    ) -> Mapping[object, Sequence[ProjectorMessage]]: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...

    def subscribe(self, topics: list[str]) -> None: ...


class ProjectorSettings(Protocol):
    projector_metrics_port: int
    redpanda_bootstrap_servers: str
    redpanda_topic_task_requested: str
    redpanda_topic_task_started: str
    redpanda_topic_task_completed: str
    redpanda_topic_task_failed: str
    redpanda_topic_task_cancelled: str
    redpanda_topic_task_expired: str


def _task_state_key(task_id: UUID) -> str:
    return f"task:{task_id}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 projector")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--poll-timeout-ms", type=int, default=1000)
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--result-ttl-seconds", type=int, default=86_400)
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


def _decode_header_value(headers: Sequence[tuple[str, bytes | str | None]], key: str) -> str | None:
    for header_key, header_value in headers:
        if header_key != key or header_value is None:
            continue
        if isinstance(header_value, bytes):
            return header_value.decode("utf-8")
        return header_value
    return None


def build_redpanda_consumer(
    settings: ProjectorSettings,
    *,
    group_id: str = "solution3-projector",
    auto_offset_reset: str = "earliest",
) -> ProjectorConsumer:
    if KafkaConsumer is None:
        raise RuntimeError("kafka-python is not installed")

    bootstrap_servers = [
        server.strip()
        for server in settings.redpanda_bootstrap_servers.split(",")
        if server.strip()
    ]
    if not bootstrap_servers:
        raise RuntimeError("redpanda bootstrap servers are not configured")

    consumer = KafkaConsumer(
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset=auto_offset_reset,
    )
    consumer.subscribe(
        [
            getattr(settings, "redpanda_topic_task_requested", REDPANDA_TOPIC_TASK_REQUESTED),
            getattr(settings, "redpanda_topic_task_started", REDPANDA_TOPIC_TASK_STARTED),
            getattr(settings, "redpanda_topic_task_completed", REDPANDA_TOPIC_TASK_COMPLETED),
            getattr(settings, "redpanda_topic_task_failed", REDPANDA_TOPIC_TASK_FAILED),
            getattr(settings, "redpanda_topic_task_cancelled", REDPANDA_TOPIC_TASK_CANCELLED),
            getattr(settings, "redpanda_topic_task_expired", REDPANDA_TOPIC_TASK_EXPIRED),
        ]
    )
    return cast(ProjectorConsumer, consumer)


async def _cache_projected_view(
    *,
    redis_client: ProjectorRedis | None,
    task: TaskQueryView,
    task_result_ttl_seconds: int,
) -> None:
    if redis_client is None:
        return

    mapping = {
        "user_id": str(task.user_id),
        "status": task.status.value,
        "billing_state": task.billing_state.value,
    }
    if task.result is not None:
        mapping["result"] = json.dumps(task.result)
    if task.error is not None:
        mapping["error"] = task.error
    await redis_client.hset(_task_state_key(task.task_id), mapping=mapping)
    await redis_client.expire(_task_state_key(task.task_id), task_result_ttl_seconds)


async def project_message(
    *,
    db_pool: asyncpg.Pool,
    redis_client: ProjectorRedis | None,
    consumer_name: str,
    projector_name: str,
    message: ProjectorMessage,
    task_result_ttl_seconds: int,
) -> bool:
    event = json.loads(message.value.decode("utf-8"))
    if not isinstance(event, dict):
        raise ValueError("projector payload must decode to an object")

    event_id_value = _decode_header_value(message.headers, "event_id")
    if event_id_value is None:
        raise ValueError("event_id header is required")
    event_id = UUID(event_id_value)

    already_processed = await is_inbox_event_processed(
        db_pool,
        event_id=event_id,
        consumer_name=consumer_name,
    )
    if already_processed:
        EVENTS_PROJECTED_TOTAL.labels(topic=message.topic, result="duplicate").inc()
        return False

    projected = await apply_task_projection(
        db_pool,
        consumer_name=consumer_name,
        projector_name=projector_name,
        topic=message.topic,
        partition_id=message.partition,
        committed_offset=message.offset,
        event_id=event_id,
        event=event,
    )
    if projected is None:
        EVENTS_PROJECTED_TOTAL.labels(topic=message.topic, result="missing").inc()
        logger.warning(
            "projector_source_task_missing",
            task_id=event.get("task_id"),
            topic=message.topic,
            event_id=str(event_id),
        )
        return False
    await _cache_projected_view(
        redis_client=redis_client,
        task=projected,
        task_result_ttl_seconds=task_result_ttl_seconds,
    )
    EVENTS_PROJECTED_TOTAL.labels(topic=message.topic, result="applied").inc()
    return True


async def project_polled_messages_async(
    *,
    consumer: ProjectorConsumer,
    db_pool: asyncpg.Pool,
    redis_client: ProjectorRedis | None,
    consumer_name: str,
    projector_name: str,
    poll_timeout_ms: int,
    max_records: int,
    task_result_ttl_seconds: int,
) -> int:
    polled = consumer.poll(timeout_ms=poll_timeout_ms, max_records=max_records)
    messages = [message for batch in polled.values() for message in batch]
    if not messages:
        return 0

    for message in messages:
        await project_message(
            db_pool=db_pool,
            redis_client=redis_client,
            consumer_name=consumer_name,
            projector_name=projector_name,
            message=message,
            task_result_ttl_seconds=task_result_ttl_seconds,
        )

    consumer.commit()
    return len(messages)


def project_polled_messages(
    *,
    consumer: ProjectorConsumer,
    db_pool: asyncpg.Pool,
    redis_client: ProjectorRedis | None,
    consumer_name: str,
    projector_name: str,
    poll_timeout_ms: int,
    max_records: int,
    task_result_ttl_seconds: int,
    run_async: Callable[[Awaitable[int]], int] | None = None,
) -> int:
    runner = run_async or asyncio.run
    return runner(
        project_polled_messages_async(
            consumer=consumer,
            db_pool=db_pool,
            redis_client=redis_client,
            consumer_name=consumer_name,
            projector_name=projector_name,
            poll_timeout_ms=poll_timeout_ms,
            max_records=max_records,
            task_result_ttl_seconds=task_result_ttl_seconds,
        )
    )


async def _main_async(
    *,
    interval_seconds: float,
    poll_timeout_ms: int,
    max_records: int,
    task_result_ttl_seconds: int,
) -> None:
    settings = load_settings()
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await redis_client.ping()
    start_http_server(settings.projector_metrics_port)
    projector_redis = cast(ProjectorRedis, redis_client)
    consumer = build_redpanda_consumer(settings)
    stop_event = asyncio.Event()
    _install_stop_handlers(stop_event)

    logger.info(
        "projector_started",
        interval_seconds=interval_seconds,
        poll_timeout_ms=poll_timeout_ms,
        max_records=max_records,
    )
    try:
        while not stop_event.is_set():
            try:
                projected = await project_polled_messages_async(
                    consumer=consumer,
                    db_pool=db_pool,
                    redis_client=projector_redis,
                    consumer_name="projector",
                    projector_name="projector",
                    poll_timeout_ms=poll_timeout_ms,
                    max_records=max_records,
                    task_result_ttl_seconds=task_result_ttl_seconds,
                )
                if projected > 0:
                    logger.info("projector_batch_projected", count=projected)
                    continue
            except Exception as exc:
                logger.exception("projector_iteration_failed", error=str(exc))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        consumer.close()
        await redis_client.close()
        await db_pool.close()
        logger.info("projector_stopped")


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    asyncio.run(
        _main_async(
            interval_seconds=max(float(args.interval), 0.1),
            poll_timeout_ms=max(int(args.poll_timeout_ms), 1),
            max_records=max(int(args.max_records), 1),
            task_result_ttl_seconds=max(int(args.result_ttl_seconds), 1),
        )
    )


if __name__ == "__main__":
    main()
