from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from solution3.models.domain import OutboxEventRecord
from solution3.workers import outbox_relay


class FakeProducer:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.flush_calls = 0

    def produce(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: Mapping[str, str],
    ) -> None:
        self.messages.append(
            {
                "topic": topic,
                "key": key,
                "value": value,
                "headers": headers,
            }
        )

    def flush(self) -> None:
        self.flush_calls += 1


def _event(*, topic: str = "tasks.requested") -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=uuid4(),
        aggregate_id=uuid4(),
        event_type="task.requested",
        topic=topic,
        payload='{"task_id":"123"}',
        created_at=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_relay_once_publishes_then_marks_events(monkeypatch: pytest.MonkeyPatch) -> None:
    producer = FakeProducer()
    marked_ids: list[UUID] = []
    events = [_event(), _event(topic="billing.captured")]

    async def fake_fetch(*_args: object, **_kwargs: object) -> list[OutboxEventRecord]:
        return events

    async def fake_mark(*_args: object, event_ids: list[UUID], **_kwargs: object) -> None:
        marked_ids.extend(event_ids)

    monkeypatch.setattr(outbox_relay, "fetch_unpublished_outbox_events", fake_fetch)
    monkeypatch.setattr(outbox_relay, "mark_outbox_events_published", fake_mark)

    relayed = await outbox_relay.relay_once(
        db_pool=cast(asyncpg.Pool, object()),
        producer=producer,
    )

    assert relayed == 2
    assert producer.flush_calls == 1
    assert [message["topic"] for message in producer.messages] == [
        "tasks.requested",
        "billing.captured",
    ]
    assert marked_ids == [event.event_id for event in events]


@pytest.mark.asyncio
async def test_relay_once_skips_flush_when_no_unpublished_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = FakeProducer()

    async def fake_fetch(*_args: object, **_kwargs: object) -> list[OutboxEventRecord]:
        return []

    async def fake_mark(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mark should not be called for an empty batch")

    monkeypatch.setattr(outbox_relay, "fetch_unpublished_outbox_events", fake_fetch)
    monkeypatch.setattr(outbox_relay, "mark_outbox_events_published", fake_mark)

    relayed = await outbox_relay.relay_once(
        db_pool=cast(asyncpg.Pool, object()),
        producer=producer,
    )

    assert relayed == 0
    assert producer.messages == []
    assert producer.flush_calls == 0


@pytest.mark.asyncio
async def test_relay_once_does_not_mark_events_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event()
    mark_called = False

    class FailingProducer(FakeProducer):
        def produce(
            self,
            *,
            topic: str,
            key: bytes,
            value: bytes,
            headers: Mapping[str, str],
        ) -> None:
            _ = (topic, key, value, headers)
            raise RuntimeError("producer unavailable")

    async def fake_fetch(*_args: object, **_kwargs: object) -> list[OutboxEventRecord]:
        return [event]

    async def fake_mark(*_args: object, **_kwargs: object) -> None:
        nonlocal mark_called
        mark_called = True

    monkeypatch.setattr(outbox_relay, "fetch_unpublished_outbox_events", fake_fetch)
    monkeypatch.setattr(outbox_relay, "mark_outbox_events_published", fake_mark)

    with pytest.raises(RuntimeError, match="producer unavailable"):
        await outbox_relay.relay_once(
            db_pool=cast(asyncpg.Pool, object()),
            producer=FailingProducer(),
        )

    assert mark_called is False
