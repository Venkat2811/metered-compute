from __future__ import annotations

import argparse
import asyncio
import json
import signal
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast
from uuid import UUID

import asyncpg
import httpx
from prometheus_client import start_http_server

from solution3.constants import (
    REDPANDA_TOPIC_TASK_CANCELLED,
    REDPANDA_TOPIC_TASK_COMPLETED,
    REDPANDA_TOPIC_TASK_EXPIRED,
    REDPANDA_TOPIC_TASK_FAILED,
)
from solution3.core.settings import load_settings
from solution3.db.repository import get_task_callback_url, insert_webhook_dead_letter
from solution3.observability.metrics import (
    WEBHOOK_DELIVERIES_TOTAL,
    WEBHOOK_DELIVERY_DURATION_SECONDS,
)
from solution3.utils.logging import configure_logging, get_logger

try:
    from kafka import KafkaConsumer
except ImportError:  # pragma: no cover - explicit runtime guard tested via monkeypatch
    KafkaConsumer = None

logger = get_logger("solution3.workers.webhook_dispatcher")


class WebhookMessage(Protocol):
    topic: str
    partition: int
    offset: int
    value: bytes
    headers: Sequence[tuple[str, bytes | str | None]]


class WebhookConsumer(Protocol):
    def poll(
        self, *, timeout_ms: int, max_records: int
    ) -> Mapping[object, Sequence[WebhookMessage]]: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...

    def subscribe(self, topics: list[str]) -> None: ...


class WebhookResponse(Protocol):
    status_code: int


class WebhookHttpClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
    ) -> WebhookResponse: ...

    async def aclose(self) -> None: ...


class WebhookSettings(Protocol):
    redpanda_bootstrap_servers: str
    redpanda_topic_task_completed: str
    redpanda_topic_task_failed: str
    redpanda_topic_task_cancelled: str
    redpanda_topic_task_expired: str
    webhook_delivery_timeout_seconds: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 webhook worker")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--poll-timeout-ms", type=int, default=1000)
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--initial-backoff-seconds", type=float, default=1.0)
    parser.add_argument("--max-backoff-seconds", type=float, default=4.0)
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


def _next_backoff_seconds(
    *,
    attempt_number: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
) -> float:
    multiplier = float(2 ** max(0, attempt_number - 1))
    return float(min(initial_backoff_seconds * multiplier, max_backoff_seconds))


def _terminal_webhook_payload(event: Mapping[str, object]) -> dict[str, object]:
    return {
        "task_id": str(event["task_id"]),
        "status": str(event["status"]),
        "billing_state": str(event["billing_state"]),
        "result": event.get("result"),
        "error": str(event["error"]) if event.get("error") is not None else None,
    }


async def _deliver_with_retries(
    *,
    http_client: WebhookHttpClient,
    callback_url: str,
    event_id: UUID,
    payload: dict[str, object],
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> tuple[bool, int, str | None]:
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await http_client.post(
                callback_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Event-Id": str(event_id),
                },
            )
            if 200 <= response.status_code < 300:
                return True, attempt, None
            last_error = f"status={response.status_code}"
            if response.status_code < 500:
                return False, attempt, last_error
        except Exception as exc:
            last_error = str(exc)

        if attempt < max_attempts:
            await sleep(
                _next_backoff_seconds(
                    attempt_number=attempt,
                    initial_backoff_seconds=initial_backoff_seconds,
                    max_backoff_seconds=max_backoff_seconds,
                )
            )

    return False, max_attempts, last_error


async def process_message(
    *,
    db_pool: asyncpg.Pool,
    http_client: WebhookHttpClient,
    message: WebhookMessage,
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    decoded = json.loads(message.value.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("webhook payload must decode to an object")

    task_id = UUID(str(decoded["task_id"]))
    event_id_value = _decode_header_value(message.headers, "event_id")
    if event_id_value is None:
        raise ValueError("event_id header is required")
    event_id = UUID(event_id_value)

    callback_url = await get_task_callback_url(db_pool, task_id=task_id)
    if not callback_url:
        WEBHOOK_DELIVERIES_TOTAL.labels(result="skipped_no_callback").inc()
        return True

    payload = _terminal_webhook_payload(decoded)
    start = time.perf_counter()
    delivered, attempts, last_error = await _deliver_with_retries(
        http_client=http_client,
        callback_url=callback_url,
        event_id=event_id,
        payload=payload,
        max_attempts=max_attempts,
        initial_backoff_seconds=initial_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        sleep=sleep,
    )
    duration = time.perf_counter() - start
    if delivered:
        WEBHOOK_DELIVERIES_TOTAL.labels(result="ok").inc()
        WEBHOOK_DELIVERY_DURATION_SECONDS.labels(result="ok").observe(duration)
        return True

    await insert_webhook_dead_letter(
        db_pool,
        event_id=event_id,
        task_id=task_id,
        topic=message.topic,
        callback_url=callback_url,
        payload=payload,
        attempts=attempts,
        last_error=last_error or "webhook_delivery_failed",
    )
    logger.warning(
        "webhook_dead_lettered",
        event_id=str(event_id),
        task_id=str(task_id),
        topic=message.topic,
        attempts=attempts,
        error=last_error,
    )
    WEBHOOK_DELIVERIES_TOTAL.labels(result="dead_letter").inc()
    WEBHOOK_DELIVERY_DURATION_SECONDS.labels(result="dead_letter").observe(duration)
    return True


def build_redpanda_consumer(
    settings: WebhookSettings,
    *,
    group_id: str = "solution3-webhook-worker",
    auto_offset_reset: str = "latest",
) -> WebhookConsumer:
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
            getattr(settings, "redpanda_topic_task_completed", REDPANDA_TOPIC_TASK_COMPLETED),
            getattr(settings, "redpanda_topic_task_failed", REDPANDA_TOPIC_TASK_FAILED),
            getattr(settings, "redpanda_topic_task_cancelled", REDPANDA_TOPIC_TASK_CANCELLED),
            getattr(settings, "redpanda_topic_task_expired", REDPANDA_TOPIC_TASK_EXPIRED),
        ]
    )
    return cast(WebhookConsumer, consumer)


async def process_polled_messages_async(
    *,
    consumer: WebhookConsumer,
    db_pool: asyncpg.Pool,
    http_client: WebhookHttpClient,
    poll_timeout_ms: int,
    max_records: int,
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    process_message_fn: Callable[..., Awaitable[bool]] = process_message,
) -> int:
    polled = consumer.poll(timeout_ms=poll_timeout_ms, max_records=max_records)
    messages = [message for batch in polled.values() for message in batch]
    if not messages:
        return 0

    processed = 0
    for message in messages:
        handled = await process_message_fn(
            db_pool=db_pool,
            http_client=http_client,
            message=message,
            max_attempts=max_attempts,
            initial_backoff_seconds=initial_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
        )
        if handled:
            consumer.commit()
            processed += 1
    return processed


def process_polled_messages(
    *,
    consumer: WebhookConsumer,
    db_pool: asyncpg.Pool,
    http_client: WebhookHttpClient,
    poll_timeout_ms: int,
    max_records: int,
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    run_async: Callable[[Awaitable[int]], int] | None = None,
    process_message_fn: Callable[..., Awaitable[bool]] = process_message,
) -> int:
    runner = run_async or asyncio.run
    return runner(
        process_polled_messages_async(
            consumer=consumer,
            db_pool=db_pool,
            http_client=http_client,
            poll_timeout_ms=poll_timeout_ms,
            max_records=max_records,
            max_attempts=max_attempts,
            initial_backoff_seconds=initial_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
            process_message_fn=process_message_fn,
        )
    )


async def _main_async(
    *,
    interval_seconds: float,
    poll_timeout_ms: int,
    max_records: int,
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
) -> None:
    settings = load_settings()
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    http_client = httpx.AsyncClient(timeout=settings.webhook_delivery_timeout_seconds)
    start_http_server(settings.webhook_metrics_port)
    consumer = build_redpanda_consumer(settings)
    stop_event = asyncio.Event()
    _install_stop_handlers(stop_event)

    logger.info(
        "webhook_worker_started",
        interval_seconds=interval_seconds,
        poll_timeout_ms=poll_timeout_ms,
        max_records=max_records,
        max_attempts=max_attempts,
    )
    try:
        while not stop_event.is_set():
            try:
                processed = await process_polled_messages_async(
                    consumer=consumer,
                    db_pool=db_pool,
                    http_client=http_client,
                    poll_timeout_ms=poll_timeout_ms,
                    max_records=max_records,
                    max_attempts=max_attempts,
                    initial_backoff_seconds=initial_backoff_seconds,
                    max_backoff_seconds=max_backoff_seconds,
                )
                if processed > 0:
                    logger.info("webhook_worker_batch_processed", count=processed)
                    continue
            except Exception as exc:
                logger.exception("webhook_worker_iteration_failed", error=str(exc))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        consumer.close()
        await http_client.aclose()
        await db_pool.close()
        logger.info("webhook_worker_stopped")


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    asyncio.run(
        _main_async(
            interval_seconds=max(float(args.interval), 0.1),
            poll_timeout_ms=max(int(args.poll_timeout_ms), 1),
            max_records=max(int(args.max_records), 1),
            max_attempts=max(int(args.max_attempts), 1),
            initial_backoff_seconds=max(float(args.initial_backoff_seconds), 0.0),
            max_backoff_seconds=max(float(args.max_backoff_seconds), 0.0),
        )
    )


if __name__ == "__main__":
    main()
