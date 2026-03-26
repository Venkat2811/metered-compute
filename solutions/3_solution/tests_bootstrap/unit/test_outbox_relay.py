from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
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
        self.close_calls = 0

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

    def close(self) -> None:
        self.close_calls += 1


class FakeKafkaFuture:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.timeouts: list[float | None] = []

    def get(self, timeout: float | None = None) -> object:
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        return {"offset": 1}


class FakeKafkaProducerClient:
    def __init__(self, futures: list[FakeKafkaFuture] | None = None) -> None:
        self.futures = list(futures or [])
        self.sent: list[dict[str, object]] = []
        self.flush_timeouts: list[float | None] = []
        self.close_timeouts: list[float | None] = []

    def send(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        headers: list[tuple[str, bytes]],
    ) -> FakeKafkaFuture:
        future = self.futures.pop(0) if self.futures else FakeKafkaFuture()
        self.sent.append(
            {
                "topic": topic,
                "key": key,
                "value": value,
                "headers": headers,
                "future": future,
            }
        )
        return future

    def flush(self, timeout: float | None = None) -> None:
        self.flush_timeouts.append(timeout)

    def close(self, timeout: float | None = None) -> None:
        self.close_timeouts.append(timeout)


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


def test_redpanda_relay_producer_flushes_and_waits_for_delivery() -> None:
    client = FakeKafkaProducerClient()
    producer = outbox_relay.RedpandaRelayProducer(
        producer=client,
        flush_timeout_seconds=7.0,
        delivery_timeout_seconds=11.0,
    )

    producer.produce(
        topic="tasks.requested",
        key=b"event-1",
        value=b'{"task_id":"123"}',
        headers={"event_id": "event-1", "event_type": "task.requested"},
    )
    producer.flush()
    producer.close()

    assert client.sent == [
        {
            "topic": "tasks.requested",
            "key": b"event-1",
            "value": b'{"task_id":"123"}',
            "headers": [("event_id", b"event-1"), ("event_type", b"task.requested")],
            "future": client.sent[0]["future"],
        }
    ]
    future = cast(FakeKafkaFuture, client.sent[0]["future"])
    assert client.flush_timeouts == [7.0]
    assert future.timeouts == [11.0]
    assert client.close_timeouts == [7.0]


def test_redpanda_relay_producer_surfaces_delivery_failures() -> None:
    future = FakeKafkaFuture(error=RuntimeError("delivery failed"))
    client = FakeKafkaProducerClient(futures=[future])
    producer = outbox_relay.RedpandaRelayProducer(producer=client)

    producer.produce(
        topic="tasks.requested",
        key=b"event-1",
        value=b"{}",
        headers={"event_id": "event-1"},
    )

    with pytest.raises(RuntimeError, match="delivery failed"):
        producer.flush()


def test_build_redpanda_producer_uses_solution_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeKafkaProducer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def send(
            self,
            topic: str,
            *,
            key: bytes,
            value: bytes,
            headers: list[tuple[str, bytes]],
        ) -> FakeKafkaFuture:
            _ = (topic, key, value, headers)
            return FakeKafkaFuture()

        def flush(self, timeout: float | None = None) -> None:
            _ = timeout

        def close(self, timeout: float | None = None) -> None:
            _ = timeout

    monkeypatch.setattr(outbox_relay, "KafkaProducer", FakeKafkaProducer)

    producer = outbox_relay.build_redpanda_producer(
        SimpleNamespace(redpanda_bootstrap_servers="redpanda:9092,redpanda:19092")
    )

    assert isinstance(producer, outbox_relay.RedpandaRelayProducer)
    assert captured["bootstrap_servers"] == ["redpanda:9092", "redpanda:19092"]
    assert captured["acks"] == "all"
    assert captured["client_id"] == "solution3-outbox-relay"


def test_main_configures_logging_and_runs_async_main(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[float, int]] = []

    async def fake_main_async(*, interval_seconds: float, batch_size: int) -> None:
        async_calls.append((interval_seconds, batch_size))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    def fake_asyncio_run(coro: object) -> None:
        assert hasattr(coro, "send")
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(
        outbox_relay,
        "_parse_args",
        lambda: SimpleNamespace(interval=2.5, batch_size=25),
    )
    monkeypatch.setattr(outbox_relay, "_main_async", fake_main_async)
    monkeypatch.setattr(outbox_relay, "configure_logging", fake_configure_logging)
    monkeypatch.setattr("solution3.workers.outbox_relay.asyncio.run", fake_asyncio_run)

    outbox_relay.main()

    assert configure_calls == [False]
    assert async_calls == [(2.5, 25)]
