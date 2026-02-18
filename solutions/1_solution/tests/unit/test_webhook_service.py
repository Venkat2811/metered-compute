from __future__ import annotations

from uuid import uuid4

import pytest

from solution1.constants import TaskStatus
from solution1.services import webhooks


def test_is_valid_callback_url_accepts_http_and_https() -> None:
    assert webhooks.is_valid_callback_url("https://example.com/callback") is True
    assert webhooks.is_valid_callback_url("http://api.vendor.com/webhook") is True


def test_is_valid_callback_url_rejects_invalid_shapes() -> None:
    assert webhooks.is_valid_callback_url("ftp://example.com/callback") is False
    assert webhooks.is_valid_callback_url("https:///missing-host") is False
    assert webhooks.is_valid_callback_url("http://localhost:9000/webhook") is False
    assert webhooks.is_valid_callback_url("http://127.0.0.1/webhook") is False
    assert webhooks.is_valid_callback_url("http://10.1.2.3/hook") is False
    assert webhooks.is_valid_callback_url("http://[::1]/hook") is False
    assert webhooks.is_valid_callback_url("") is False
    assert webhooks.is_valid_callback_url(" " * 10) is False


def test_next_retry_delay_seconds_uses_exponential_backoff_with_cap() -> None:
    assert (
        webhooks.next_retry_delay_seconds(
            attempt=1,
            initial_seconds=1.0,
            multiplier=2.0,
            max_seconds=30.0,
        )
        == 1.0
    )
    assert (
        webhooks.next_retry_delay_seconds(
            attempt=4,
            initial_seconds=1.0,
            multiplier=2.0,
            max_seconds=30.0,
        )
        == 8.0
    )
    assert (
        webhooks.next_retry_delay_seconds(
            attempt=10,
            initial_seconds=1.0,
            multiplier=2.0,
            max_seconds=30.0,
        )
        == 30.0
    )


def test_webhook_event_round_trip_serialization() -> None:
    event = webhooks.build_terminal_webhook_event(
        user_id=uuid4(),
        task_id=uuid4(),
        status=TaskStatus.COMPLETED.value,
        result={"z": 7},
        error=None,
    )
    serialized = webhooks.serialize_webhook_event(event)
    parsed = webhooks.parse_webhook_event(serialized)

    assert parsed is not None
    assert parsed.event_id == event.event_id
    assert parsed.status == TaskStatus.COMPLETED.value
    assert parsed.result == {"z": 7}


@pytest.mark.asyncio
async def test_enqueue_terminal_webhook_event_trims_queue_to_maxlen() -> None:
    class _FakeRedis:
        def __init__(self) -> None:
            self.values: list[str] = []

        async def rpush(self, _: str, value: str) -> int:
            self.values.append(value)
            return len(self.values)

        async def ltrim(self, _: str, start: int, end: int) -> bool:
            length = len(self.values)
            start_idx = start if start >= 0 else length + start
            end_idx = end if end >= 0 else length + end
            start_idx = max(0, start_idx)
            end_idx = min(length - 1, end_idx)
            if end_idx < start_idx:
                self.values = []
            else:
                self.values = self.values[start_idx : end_idx + 1]
            return True

    redis_client = _FakeRedis()
    for _ in range(3):
        event = webhooks.build_terminal_webhook_event(
            user_id=uuid4(),
            task_id=uuid4(),
            status=TaskStatus.COMPLETED.value,
            result={"z": 1},
            error=None,
        )
        await webhooks.enqueue_terminal_webhook_event(
            redis_client=redis_client,  # type: ignore[arg-type]
            queue_key="webhook:queue",
            event=event,
            max_queue_length=2,
        )

    assert len(redis_client.values) == 2
