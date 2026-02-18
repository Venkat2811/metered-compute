"""Webhook helpers for callback validation, event envelopes, and retry scheduling."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from redis.asyncio import Redis
from uuid6 import uuid7

from solution1.models.domain import WebhookTerminalEvent

MAX_CALLBACK_URL_LENGTH = 2048
_ALLOWED_CALLBACK_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTNAMES = frozenset({"localhost"})
_BLOCKED_HOST_SUFFIXES = frozenset({".localhost", ".local", ".internal", ".home.arpa"})

__all__ = [
    "MAX_CALLBACK_URL_LENGTH",
    "WebhookTerminalEvent",
    "build_terminal_webhook_event",
    "enqueue_terminal_webhook_event",
    "is_safe_callback_hostname",
    "is_valid_callback_url",
    "next_retry_delay_seconds",
    "parse_webhook_event",
    "serialize_webhook_event",
]


def is_valid_callback_url(callback_url: str) -> bool:
    """Validate callback URL shape for webhook registration."""
    value = callback_url.strip()
    if not value or len(value) > MAX_CALLBACK_URL_LENGTH:
        return False
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _ALLOWED_CALLBACK_SCHEMES:
        return False
    hostname = parsed.hostname
    if hostname is None:
        return False
    return is_safe_callback_hostname(hostname)


def is_safe_callback_hostname(hostname: str) -> bool:
    """Reject localhost/private/reserved callback targets."""
    normalized = hostname.strip().rstrip(".").lower()
    if not normalized:
        return False
    if normalized in _BLOCKED_HOSTNAMES:
        return False
    if any(normalized.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        return False
    try:
        target_ip = ip_address(normalized)
    except ValueError:
        return True
    return bool(target_ip.is_global)


def build_terminal_webhook_event(
    *,
    user_id: UUID,
    task_id: UUID,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
) -> WebhookTerminalEvent:
    """Build a terminal-task webhook event payload."""
    return WebhookTerminalEvent(
        event_id=str(uuid7()),
        user_id=str(user_id),
        task_id=str(task_id),
        status=status,
        result=result,
        error=error,
        occurred_at_epoch=int(time.time()),
    )


def serialize_webhook_event(event: WebhookTerminalEvent) -> str:
    """Serialize webhook event to a queue-safe JSON string."""
    return json.dumps(asdict(event), separators=(",", ":"), sort_keys=True)


def parse_webhook_event(payload: str) -> WebhookTerminalEvent | None:
    """Parse webhook event from queue payload."""
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None

    required_fields = {
        "event_id",
        "user_id",
        "task_id",
        "status",
        "occurred_at_epoch",
        "attempt",
    }
    if not required_fields.issubset(raw):
        return None

    try:
        return WebhookTerminalEvent(
            event_id=str(raw["event_id"]),
            user_id=str(raw["user_id"]),
            task_id=str(raw["task_id"]),
            status=str(raw["status"]),
            result=raw.get("result") if isinstance(raw.get("result"), dict) else None,
            error=str(raw["error"]) if raw.get("error") is not None else None,
            occurred_at_epoch=int(raw["occurred_at_epoch"]),
            attempt=int(raw.get("attempt", 0)),
            last_error=str(raw["last_error"]) if raw.get("last_error") is not None else None,
        )
    except (TypeError, ValueError):
        return None


def next_retry_delay_seconds(
    *,
    attempt: int,
    initial_seconds: float,
    multiplier: float,
    max_seconds: float,
) -> float:
    """Compute bounded exponential backoff delay for webhook retries."""
    normalized_attempt = max(1, attempt)
    delay = initial_seconds * (multiplier ** (normalized_attempt - 1))
    return min(max_seconds, max(0.0, delay))


async def enqueue_terminal_webhook_event(
    *,
    redis_client: Redis[str],
    queue_key: str,
    event: WebhookTerminalEvent,
    max_queue_length: int | None = None,
) -> None:
    """Append a terminal-task webhook event to the pending queue."""
    payload = serialize_webhook_event(event)
    await redis_client.rpush(queue_key, payload)

    if max_queue_length is not None and max_queue_length > 0:
        await redis_client.ltrim(queue_key, -max_queue_length, -1)
