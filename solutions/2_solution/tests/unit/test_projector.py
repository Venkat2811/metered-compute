from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from solution2.constants import TaskStatus
from solution2.workers import projector
from solution2.workers.worker import RabbitMQDelivery


class _NullSpan:
    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self) -> None:
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


def _delivery(
    *,
    event_type: str = "task.submitted",
    payload_overrides: dict[str, object] | None = None,
) -> RabbitMQDelivery:
    payload: dict[str, object] = {
        "event_id": str(uuid4()),
        "task_id": str(uuid4()),
        "user_id": str(uuid4()),
        "mode": "async",
        "tier": "free",
        "model_class": "small",
        "queue": "queue.batch",
        "event_type": event_type,
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return RabbitMQDelivery(
        queue_name="queue.projector",
        routing_key="tasks.batch.free.small",
        delivery_tag=99,
        message_id=None,
        body=json.dumps(payload),
    )


def _runtime() -> projector.ProjectorRuntime:
    settings = SimpleNamespace(
        task_result_ttl_seconds=86_400,
        redis_task_state_ttl_seconds=86_400,
    )
    fake_consumer = SimpleNamespace()
    return projector.ProjectorRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, _FakePool()),
        redis_client=cast(Any, _FakeRedis()),
        consumer=cast(Any, fake_consumer),
    )


def test_parse_projection_event_submitted_defaults_to_pending() -> None:
    delivery = _delivery(event_type="task.submitted")

    parsed = projector._parse_projection_event(delivery)

    assert parsed is not None
    assert parsed.status == TaskStatus.PENDING
    assert parsed.event_type == "task.submitted"


def test_parse_projection_event_invalid_status_is_rejected() -> None:
    delivery = _delivery(payload_overrides={"status": "NOPE"})

    parsed = projector._parse_projection_event(delivery)

    assert parsed is None


@pytest.mark.asyncio
async def test_process_delivery_projected(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    delivery = _delivery(
        event_type="task.completed",
        payload_overrides={"status": "COMPLETED", "result": {"z": 12}, "runtime_ms": 25},
    )
    writes: list[str] = []

    async def fake_persist_projection(
        *,
        runtime: projector.ProjectorRuntime,
        event: projector.ProjectionEvent,
    ) -> tuple[bool, str | None, UUID | None]:
        _ = runtime
        writes.append(event.event_type)
        return True, "queue.batch", UUID("47b47338-5355-4edc-860b-846d71a2a75a")

    async def fake_write_projection_cache(**_: object) -> None:
        writes.append("cache")

    monkeypatch.setattr(projector, "start_span", lambda **_: _NullSpan())
    monkeypatch.setattr(projector, "_persist_projection", fake_persist_projection)
    monkeypatch.setattr(projector, "_write_projection_cache", fake_write_projection_cache)

    result, event_type = await projector._process_delivery(runtime=runtime, delivery=delivery)

    assert result == "projected"
    assert event_type == "task.completed"
    assert writes == ["task.completed", "cache"]


@pytest.mark.asyncio
async def test_process_delivery_invalid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    delivery = RabbitMQDelivery(
        queue_name="queue.projector",
        routing_key="tasks.batch.free.small",
        delivery_tag=1,
        message_id=None,
        body="{bad",
    )
    monkeypatch.setattr(projector, "start_span", lambda **_: _NullSpan())

    result, event_type = await projector._process_delivery(runtime=runtime, delivery=delivery)

    assert result == "invalid"
    assert event_type == "unknown"


@pytest.mark.asyncio
async def test_main_async_single_cycle_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeLoop:
        def add_signal_handler(self, *_: object) -> None:
            return None

        def remove_signal_handler(self, *_: object) -> None:
            return None

    class _OneShotEvent:
        def __init__(self) -> None:
            self._checks = 0

        def is_set(self) -> bool:
            self._checks += 1
            return self._checks > 1

        def set(self) -> None:
            self._checks = 2

    class _FakeConsumer:
        def __init__(self) -> None:
            self.connected = False
            self.queues_declared = False
            self.closed = False

        def connect(self) -> None:
            self.connected = True

        def ensure_queues(self, *, queue_names: tuple[str, ...]) -> None:
            _ = queue_names
            self.queues_declared = True

        def get_one(self, *, queue_names: tuple[str, ...]) -> RabbitMQDelivery | None:
            _ = queue_names
            return None

        def ack(self, *, delivery_tag: int) -> None:
            _ = delivery_tag

        def nack(self, *, delivery_tag: int, requeue: bool) -> None:
            _ = (delivery_tag, requeue)

        def close(self) -> None:
            self.closed = True

    fake_pool = _FakePool()
    fake_redis = _FakeRedis()
    fake_consumer = _FakeConsumer()

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_build_db_pool(*_: object, **__: object) -> _FakePool:
        return fake_pool

    monkeypatch.setattr(projector, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(projector, "_build_db_pool", fake_build_db_pool)
    monkeypatch.setattr(
        "solution2.workers.projector.Redis.from_url",
        lambda *_args, **_kwargs: fake_redis,
    )
    monkeypatch.setattr(projector, "RabbitMQTaskConsumer", lambda **_: fake_consumer)
    monkeypatch.setattr(projector, "start_http_server", lambda *_: None)
    monkeypatch.setattr("solution2.workers.projector.asyncio.Event", _OneShotEvent)
    monkeypatch.setattr("solution2.workers.projector.asyncio.get_running_loop", lambda: _FakeLoop())
    monkeypatch.setattr(
        projector,
        "load_settings",
        lambda: cast(
            Any,
            SimpleNamespace(
                app_name="mc-solution2",
                postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
                redis_url="redis://localhost:6379/0",
                rabbitmq_url="amqp://guest:guest@rabbitmq:5672/",
                db_pool_min_size=1,
                db_pool_max_size=2,
                db_pool_command_timeout_seconds=0.1,
                db_statement_timeout_batch_ms=1000,
                db_idle_in_transaction_timeout_ms=500,
                db_pool_max_inactive_connection_lifetime_seconds=60.0,
                redis_socket_timeout_seconds=0.1,
                redis_socket_connect_timeout_seconds=0.1,
                worker_db_timeout_seconds=3.0,
                projector_metrics_port=9300,
            ),
        ),
    )

    await projector.main_async()

    assert fake_consumer.connected is True
    assert fake_consumer.queues_declared is True
    assert fake_consumer.closed is True
    assert fake_pool.closed is True
    assert fake_redis.closed is True
