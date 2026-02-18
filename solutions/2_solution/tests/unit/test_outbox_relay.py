from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from solution2.models.domain import OutboxEvent
from solution2.workers import outbox_relay


@dataclass
class _FakeGauge:
    values: list[float]

    def __init__(self) -> None:
        self.values = []

    def set(self, value: float) -> None:
        self.values.append(value)


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeRabbitMQ:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict[str, Any]]] = []

    def publish(self, *, event_id: str, routing_key: str, payload: dict[str, Any]) -> None:
        self.published.append((event_id, routing_key, payload))


class _FakePoolForMain:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeRelayForMain(_FakeRabbitMQ):
    def __init__(self) -> None:
        super().__init__()
        self.connected = False
        self.topology = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def ensure_topology(self) -> None:
        self.topology = True

    def close(self) -> None:
        self.closed = True


class _OneShotEvent:
    def __init__(self) -> None:
        self._checks = 0

    def is_set(self) -> bool:
        self._checks += 1
        return self._checks > 1

    def set(self) -> None:
        self._checks = 2


class _FakeLoop:
    def add_signal_handler(self, *_: object) -> None:
        return None

    def remove_signal_handler(self, *_: object) -> None:
        return None


def _runtime_settings() -> SimpleNamespace:
    return SimpleNamespace(
        outbox_relay_batch_size=5,
        outbox_relay_empty_backoff_seconds=0.05,
        outbox_relay_error_backoff_seconds=0.05,
        outbox_relay_connect_timeout_seconds=3.0,
        outbox_relay_purge_interval_seconds=3600.0,
        outbox_relay_purge_retention_seconds=604_800,
        outbox_relay_purge_batch_size=500,
        outbox_relay_metrics_port=9200,
        app_name="mc-solution2",
        postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
        db_pool_min_size=1,
        db_pool_max_size=8,
        db_pool_command_timeout_seconds=0.1,
        db_statement_timeout_ms=50,
        db_idle_in_transaction_timeout_ms=500,
        db_pool_max_inactive_connection_lifetime_seconds=300.0,
        rabbitmq_url="amqp://guest:guest@rabbitmq:5672/",
    )


def _runtime(settings: SimpleNamespace) -> outbox_relay.OutboxRelayRuntime:
    return outbox_relay.OutboxRelayRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, _FakePool()),
        rabbitmq=cast(Any, _FakeRabbitMQ()),
    )


@pytest.mark.asyncio
async def test_publish_batch_updates_lag_metric_and_marks_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gauge = _FakeGauge()
    now = int(time.time())
    events = [
        OutboxEvent(
            event_id=uuid4(),
            aggregate_id=uuid4(),
            event_type="task.submitted",
            routing_key="tasks.batch.free.small",
            payload={"x": 1},
            published_at=None,
            created_at=datetime.fromtimestamp(now - 12, tz=UTC),
        ),
        OutboxEvent(
            event_id=uuid4(),
            aggregate_id=uuid4(),
            event_type="task.submitted",
            routing_key="tasks.batch.free.small",
            payload={"x": 2},
            published_at=None,
            created_at=datetime.fromtimestamp(now - 8, tz=UTC),
        ),
    ]
    mark_calls: list[UUID] = []

    async def fake_list(*_: object, **__: object) -> list[OutboxEvent]:
        return events

    async def fake_mark(*_: object, event_id: UUID, **__: object) -> bool:
        mark_calls.append(event_id)
        return True

    monkeypatch.setattr(outbox_relay, "list_unpublished_outbox_events", fake_list)
    monkeypatch.setattr(outbox_relay, "mark_outbox_event_published", fake_mark)
    monkeypatch.setattr(outbox_relay, "OUTBOX_PUBLISH_LAG_SECONDS", gauge)

    settings = _runtime_settings()
    runtime = _runtime(settings)
    published = await outbox_relay._publish_batch(runtime=runtime)
    rabbitmq = cast(_FakeRabbitMQ, runtime.rabbitmq)

    assert published == 2
    assert len(gauge.values) == 1
    assert gauge.values[0] >= 0.0
    assert len(rabbitmq.published) == 2
    assert rabbitmq.published[0][1] == "tasks.batch.free.small"
    assert rabbitmq.published[0][2]["aggregate_id"] == str(events[0].aggregate_id)
    assert rabbitmq.published[0][2]["event_type"] == "task.submitted"
    assert rabbitmq.published[0][2]["event_id"] == str(events[0].event_id)
    assert mark_calls == [events[0].event_id, events[1].event_id]


@pytest.mark.asyncio
async def test_publish_batch_returns_zero_when_no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    gauge = _FakeGauge()

    async def fake_empty(*_: object, **__: object) -> list[OutboxEvent]:
        return []

    monkeypatch.setattr(outbox_relay, "list_unpublished_outbox_events", fake_empty)
    monkeypatch.setattr(outbox_relay, "OUTBOX_PUBLISH_LAG_SECONDS", gauge)

    runtime = _runtime(_runtime_settings())
    published = await outbox_relay._publish_batch(runtime=runtime)

    assert published == 0
    assert gauge.values == [0.0]


@pytest.mark.asyncio
async def test_publish_batch_stops_on_first_publish_error(monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        OutboxEvent(
            event_id=uuid4(),
            aggregate_id=uuid4(),
            event_type="task.submitted",
            routing_key="tasks.batch.free.small",
            payload={},
            published_at=None,
            created_at=datetime.fromtimestamp(now, tz=UTC),
        ),
        OutboxEvent(
            event_id=uuid4(),
            aggregate_id=uuid4(),
            event_type="task.submitted",
            routing_key="tasks.batch.free.small",
            payload={},
            published_at=None,
            created_at=datetime.fromtimestamp(now - 1, tz=UTC),
        ),
    ]

    class _FailingRelay(_FakeRabbitMQ):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def publish(self, *, event_id: str, routing_key: str, payload: dict[str, Any]) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("publish failed")
            super().publish(event_id=event_id, routing_key=routing_key, payload=payload)

    async def fake_list(*_: object, **__: object) -> list[OutboxEvent]:
        return events

    monkeypatch.setattr(outbox_relay, "list_unpublished_outbox_events", fake_list)
    monkeypatch.setattr(outbox_relay, "mark_outbox_event_published", lambda *_, **__: False)

    settings = _runtime_settings()
    failing_relay = _FailingRelay()
    runtime = outbox_relay.OutboxRelayRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, _FakePool()),
        rabbitmq=cast(Any, failing_relay),
    )
    published = await outbox_relay._publish_batch(runtime=runtime)

    assert published == 0
    assert failing_relay.calls == 1


@pytest.mark.asyncio
async def test_main_async_runs_one_cycle_and_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _runtime_settings()
    fake_pool = _FakePoolForMain()
    fake_relay = _FakeRelayForMain()
    publish_calls = {"count": 0}
    purge_calls = {"count": 0}
    publish_cycle_results = [0]

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_create_pool(**_: object) -> _FakePoolForMain:
        return fake_pool

    async def fake_publish_cycle(*_: object, **__: object) -> int:
        publish_calls["count"] += 1
        return publish_cycle_results[0]

    async def fake_purge_cycle(*_: object, **__: object) -> int:
        purge_calls["count"] += 1
        return 0

    monkeypatch.setattr(outbox_relay, "run_migrations", fake_run_migrations)
    monkeypatch.setattr("solution2.workers.outbox_relay.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr("solution2.workers.outbox_relay._publish_batch", fake_publish_cycle)
    monkeypatch.setattr("solution2.workers.outbox_relay._purge_published_events", fake_purge_cycle)
    monkeypatch.setattr(
        "solution2.workers.outbox_relay.asyncio.Event",
        _OneShotEvent,
    )
    monkeypatch.setattr(
        "solution2.workers.outbox_relay.asyncio.get_running_loop",
        lambda: _FakeLoop(),
    )
    monkeypatch.setattr("solution2.workers.outbox_relay.start_http_server", lambda *_args: None)
    monkeypatch.setattr(
        outbox_relay,
        "RabbitMQRelay",
        lambda **_: fake_relay,
    )
    monkeypatch.setattr(
        outbox_relay,
        "load_settings",
        lambda: settings,
    )

    await outbox_relay.main_async()

    assert publish_calls["count"] == 1
    assert purge_calls["count"] == 0
    assert fake_relay.connected is True
    assert fake_relay.topology is True
    assert fake_relay.closed is True
    assert fake_pool.closed is True
