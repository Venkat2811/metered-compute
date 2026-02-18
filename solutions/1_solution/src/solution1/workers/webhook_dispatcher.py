"""Webhook delivery worker with retry/backoff and dead-letter handling."""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import asdict, dataclass
from uuid import UUID

import asyncpg
import httpx
from opentelemetry.trace import SpanKind
from prometheus_client import start_http_server
from redis.asyncio import Redis

from solution1.core.settings import AppSettings, load_settings
from solution1.db.migrate import run_migrations
from solution1.db.repository import (
    get_webhook_subscription,
    insert_webhook_dead_letter,
)
from solution1.observability.metrics import (
    WEBHOOK_DELIVERIES_TOTAL,
    WEBHOOK_DELIVERY_DURATION_SECONDS,
    WEBHOOK_DLQ_DEPTH,
    WEBHOOK_QUEUE_DEPTH,
    WEBHOOK_SCHEDULED_DEPTH,
)
from solution1.observability.tracing import configure_process_tracing, start_span
from solution1.services.webhooks import (
    WebhookTerminalEvent,
    is_valid_callback_url,
    next_retry_delay_seconds,
    parse_webhook_event,
    serialize_webhook_event,
)
from solution1.utils.logging import configure_logging, get_logger

logger = get_logger("solution1.workers.webhook_dispatcher")


@dataclass
class DispatcherRuntime:
    settings: AppSettings
    db_pool: asyncpg.Pool
    redis_client: Redis[str]
    http_client: httpx.AsyncClient


def _queue_key(settings: object) -> str:
    return str(getattr(settings, "webhook_queue_key", "webhook:queue"))


def _scheduled_key(settings: object) -> str:
    return str(getattr(settings, "webhook_scheduled_key", "webhook:scheduled"))


def _dlq_key(settings: object) -> str:
    return str(getattr(settings, "webhook_dlq_key", "webhook:dlq"))


def _dispatch_batch_size(settings: object) -> int:
    return int(getattr(settings, "webhook_dispatch_batch_size", 100))


def _delivery_timeout_seconds(settings: object) -> float:
    return float(getattr(settings, "webhook_delivery_timeout_seconds", 3.0))


def _max_attempts(settings: object) -> int:
    return int(getattr(settings, "webhook_max_attempts", 5))


async def _refresh_depth_metrics(runtime: DispatcherRuntime) -> None:
    queue_size = await runtime.redis_client.llen(_queue_key(runtime.settings))
    dlq_size = await runtime.redis_client.llen(_dlq_key(runtime.settings))
    scheduled_size = await runtime.redis_client.zcard(_scheduled_key(runtime.settings))
    WEBHOOK_QUEUE_DEPTH.set(float(queue_size))
    WEBHOOK_DLQ_DEPTH.set(float(dlq_size))
    WEBHOOK_SCHEDULED_DEPTH.set(float(scheduled_size))


async def _pop_pending_event(runtime: DispatcherRuntime) -> str | None:
    timeout_seconds = int(getattr(runtime.settings, "webhook_dispatcher_poll_timeout_seconds", 2))
    item = await runtime.redis_client.blpop(_queue_key(runtime.settings), timeout=timeout_seconds)
    if item is None:
        return None
    _queue, payload = item
    return str(payload)


async def _promote_scheduled_events(runtime: DispatcherRuntime) -> int:
    now_ms = int(time.time() * 1000)
    raw_events = await runtime.redis_client.zrangebyscore(
        _scheduled_key(runtime.settings),
        min="-inf",
        max=now_ms,
        start=0,
        num=_dispatch_batch_size(runtime.settings),
    )
    promoted = 0
    for raw_event in raw_events:
        removed = await runtime.redis_client.zrem(_scheduled_key(runtime.settings), raw_event)
        if not removed:
            continue
        await runtime.redis_client.lpush(_queue_key(runtime.settings), raw_event)
        promoted += 1
    return promoted


def _event_payload(event: WebhookTerminalEvent) -> dict[str, object]:
    return {
        "event_type": "task.terminal",
        "event_id": event.event_id,
        "task_id": event.task_id,
        "user_id": event.user_id,
        "status": event.status,
        "result": event.result,
        "error": event.error,
        "occurred_at_epoch": event.occurred_at_epoch,
    }


async def _send_webhook(
    runtime: DispatcherRuntime,
    *,
    callback_url: str,
    event: WebhookTerminalEvent,
) -> None:
    started = time.perf_counter()
    payload = _event_payload(event)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event-Id": event.event_id,
    }
    response = await runtime.http_client.post(
        callback_url,
        json=payload,
        headers=headers,
    )
    duration = time.perf_counter() - started
    if 200 <= response.status_code < 300:
        WEBHOOK_DELIVERY_DURATION_SECONDS.labels(result="ok").observe(duration)
        return
    WEBHOOK_DELIVERY_DURATION_SECONDS.labels(result="error").observe(duration)
    raise RuntimeError(f"webhook delivery failed: status={response.status_code}")


async def _schedule_retry(
    runtime: DispatcherRuntime,
    *,
    event: WebhookTerminalEvent,
    error_message: str,
) -> None:
    next_attempt = event.attempt + 1
    retry_delay_seconds = next_retry_delay_seconds(
        attempt=next_attempt,
        initial_seconds=float(getattr(runtime.settings, "webhook_initial_backoff_seconds", 1.0)),
        multiplier=float(getattr(runtime.settings, "webhook_backoff_multiplier", 2.0)),
        max_seconds=float(getattr(runtime.settings, "webhook_max_backoff_seconds", 60.0)),
    )
    retried_event = WebhookTerminalEvent(
        event_id=event.event_id,
        user_id=event.user_id,
        task_id=event.task_id,
        status=event.status,
        result=event.result,
        error=event.error,
        occurred_at_epoch=event.occurred_at_epoch,
        attempt=next_attempt,
        last_error=error_message,
    )
    due_ms = int((time.time() + retry_delay_seconds) * 1000)
    await runtime.redis_client.zadd(
        _scheduled_key(runtime.settings),
        {serialize_webhook_event(retried_event): float(due_ms)},
    )


async def _send_to_dead_letter(
    runtime: DispatcherRuntime,
    *,
    event: WebhookTerminalEvent,
    error_message: str,
) -> None:
    dead_letter_event = WebhookTerminalEvent(
        event_id=event.event_id,
        user_id=event.user_id,
        task_id=event.task_id,
        status=event.status,
        result=event.result,
        error=event.error,
        occurred_at_epoch=event.occurred_at_epoch,
        attempt=event.attempt + 1,
        last_error=error_message,
    )
    raw_payload = serialize_webhook_event(dead_letter_event)
    await runtime.redis_client.rpush(_dlq_key(runtime.settings), raw_payload)
    try:
        await insert_webhook_dead_letter(
            runtime.db_pool,
            user_id=UUID(dead_letter_event.user_id),
            task_id=UUID(dead_letter_event.task_id),
            event_payload=asdict(dead_letter_event),
            last_error=error_message,
        )
    except Exception as exc:
        logger.exception(
            "webhook_dead_letter_persist_failed",
            event_id=dead_letter_event.event_id,
            error=str(exc),
        )


async def _process_raw_event(
    runtime: DispatcherRuntime,
    *,
    raw_event: str,
) -> None:
    event = parse_webhook_event(raw_event)
    if event is None:
        WEBHOOK_DELIVERIES_TOTAL.labels(result="invalid_event").inc()
        return

    with start_span(
        tracer_name="solution1.webhook_dispatcher",
        span_name="webhook.dispatch",
        kind=SpanKind.CONSUMER,
        attributes={
            "webhook.event_id": event.event_id,
            "webhook.task_id": event.task_id,
            "webhook.attempt": event.attempt,
        },
    ):
        try:
            subscription = await get_webhook_subscription(
                runtime.db_pool,
                user_id=UUID(event.user_id),
            )
        except Exception as exc:
            await _schedule_retry(runtime, event=event, error_message=f"subscription_lookup:{exc}")
            WEBHOOK_DELIVERIES_TOTAL.labels(result="retry_subscription_lookup").inc()
            return

        if subscription is None or not subscription.enabled:
            WEBHOOK_DELIVERIES_TOTAL.labels(result="skipped_no_subscription").inc()
            return
        if not is_valid_callback_url(subscription.callback_url):
            WEBHOOK_DELIVERIES_TOTAL.labels(result="skipped_invalid_subscription").inc()
            return

        try:
            await _send_webhook(runtime, callback_url=subscription.callback_url, event=event)
            WEBHOOK_DELIVERIES_TOTAL.labels(result="ok").inc()
        except Exception as exc:
            error_message = str(exc)
            if event.attempt + 1 >= _max_attempts(runtime.settings):
                await _send_to_dead_letter(runtime, event=event, error_message=error_message)
                WEBHOOK_DELIVERIES_TOTAL.labels(result="dead_letter").inc()
                return
            await _schedule_retry(runtime, event=event, error_message=error_message)
            WEBHOOK_DELIVERIES_TOTAL.labels(result="retry_scheduled").inc()


async def main_async() -> None:
    configure_logging()
    settings = load_settings()
    if not settings.webhook_enabled:
        logger.info("webhook_dispatcher_disabled")
        return
    base_service_name = str(getattr(settings, "app_name", "mc-solution1"))
    configure_process_tracing(settings=settings, service_name=f"{base_service_name}-webhook")

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
    http_client = httpx.AsyncClient(
        timeout=_delivery_timeout_seconds(settings),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )

    runtime = DispatcherRuntime(
        settings=settings,
        db_pool=db_pool,
        redis_client=redis_client,
        http_client=http_client,
    )

    try:
        start_http_server(settings.webhook_metrics_port)
    except OSError:
        logger.warning("webhook_metrics_port_in_use", port=settings.webhook_metrics_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("webhook_dispatcher_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        while not stop_event.is_set():
            try:
                await _promote_scheduled_events(runtime)
                raw_event = await _pop_pending_event(runtime)
                if raw_event is not None:
                    await _process_raw_event(runtime, raw_event=raw_event)
                await _refresh_depth_metrics(runtime)
            except Exception as exc:
                logger.exception("webhook_dispatcher_iteration_failed", error=str(exc))
                await asyncio.sleep(settings.webhook_dispatch_error_backoff_seconds)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await http_client.aclose()
        await redis_client.close()
        await db_pool.close()
        logger.info("webhook_dispatcher_shutdown_complete")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
