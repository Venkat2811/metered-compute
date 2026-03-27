from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from solution3.constants import BillingState, ModelClass, RequestMode, SubscriptionTier, TaskStatus
from solution3.models.domain import TaskQueryView
from solution3.workers import projector


class FakeMessage:
    def __init__(
        self,
        *,
        topic: str,
        partition: int,
        offset: int,
        payload: dict[str, object],
        headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.value = json.dumps(payload).encode("utf-8")
        self.headers = headers or []


class FakeConsumer:
    def __init__(self, records: dict[object, list[FakeMessage]] | None = None) -> None:
        self.records = records or {}
        self.poll_calls: list[tuple[int, int]] = []
        self.commit_calls = 0
        self.closed = False
        self.subscribed_topics: list[str] = []

    def poll(self, *, timeout_ms: int, max_records: int) -> dict[object, list[FakeMessage]]:
        self.poll_calls.append((timeout_ms, max_records))
        return self.records

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.closed = True

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics.extend(topics)


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes[key] = {**self.hashes.get(key, {}), **mapping}
        return 1

    async def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True


def _query_view(*, task_id: UUID, status: TaskStatus, billing_state: BillingState) -> TaskQueryView:
    return TaskQueryView(
        task_id=task_id,
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=status,
        billing_state=billing_state,
        result={"sum": 5} if status == TaskStatus.COMPLETED else None,
        error=None,
        runtime_ms=None,
        projection_version=9,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_project_message_skips_duplicate_events(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid4()
    message = FakeMessage(
        topic="tasks.completed",
        partition=0,
        offset=12,
        payload={"task_id": str(task_id), "result": {"sum": 5}, "error": None},
        headers=[("event_id", str(uuid4()).encode("utf-8"))],
    )
    redis = FakeRedis()

    async def fake_seen(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_apply(*_args: object, **_kwargs: object) -> TaskQueryView:
        raise AssertionError("duplicate messages must not re-apply projection")

    monkeypatch.setattr(projector, "is_inbox_event_processed", fake_seen)
    monkeypatch.setattr(projector, "apply_task_projection", fake_apply)

    projected = await projector.project_message(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(projector.ProjectorRedis, redis),
        consumer_name="projector",
        projector_name="projector",
        message=cast(projector.ProjectorMessage, message),
        task_result_ttl_seconds=300,
    )

    assert projected is False
    assert redis.hashes == {}


@pytest.mark.asyncio
async def test_project_message_applies_projection_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    message = FakeMessage(
        topic="tasks.completed",
        partition=0,
        offset=14,
        payload={"task_id": str(task_id), "result": {"sum": 5}, "error": None},
        headers=[("event_id", str(uuid4()).encode("utf-8"))],
    )
    redis = FakeRedis()
    apply_calls: list[dict[str, object]] = []

    async def fake_seen(*_args: object, **_kwargs: object) -> bool:
        return False

    async def fake_apply(*_args: object, **kwargs: object) -> TaskQueryView:
        apply_calls.append(kwargs)
        return _query_view(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            billing_state=BillingState.CAPTURED,
        )

    monkeypatch.setattr(projector, "is_inbox_event_processed", fake_seen)
    monkeypatch.setattr(projector, "apply_task_projection", fake_apply)

    projected = await projector.project_message(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(projector.ProjectorRedis, redis),
        consumer_name="projector",
        projector_name="projector",
        message=cast(projector.ProjectorMessage, message),
        task_result_ttl_seconds=600,
    )

    assert projected is True
    assert apply_calls[0]["topic"] == "tasks.completed"
    assert apply_calls[0]["committed_offset"] == 14
    assert redis.hashes[f"task:{task_id}"]["status"] == "COMPLETED"
    assert redis.hashes[f"task:{task_id}"]["billing_state"] == "CAPTURED"
    assert redis.hashes[f"task:{task_id}"]["result"] == '{"sum": 5}'
    assert redis.expirations[f"task:{task_id}"] == 600


@pytest.mark.asyncio
async def test_project_message_skips_events_when_source_task_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    message = FakeMessage(
        topic="tasks.completed",
        partition=0,
        offset=21,
        payload={"task_id": str(task_id), "result": {"sum": 5}, "error": None},
        headers=[("event_id", str(uuid4()).encode("utf-8"))],
    )
    redis = FakeRedis()

    async def fake_seen(*_args: object, **_kwargs: object) -> bool:
        return False

    async def fake_apply(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(projector, "is_inbox_event_processed", fake_seen)
    monkeypatch.setattr(projector, "apply_task_projection", fake_apply)

    projected = await projector.project_message(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(projector.ProjectorRedis, redis),
        consumer_name="projector",
        projector_name="projector",
        message=cast(projector.ProjectorMessage, message),
        task_result_ttl_seconds=600,
    )

    assert projected is False
    assert redis.hashes == {}


def test_project_polled_messages_applies_batch_and_commits_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    consumer = FakeConsumer(
        records={
            object(): [
                FakeMessage(
                    topic="tasks.requested",
                    partition=0,
                    offset=1,
                    payload={"task_id": str(task_id)},
                    headers=[("event_id", str(uuid4()).encode("utf-8"))],
                )
            ]
        }
    )
    calls: list[UUID] = []

    async def fake_project_message(*_args: object, **kwargs: object) -> bool:
        message = cast(projector.ProjectorMessage, kwargs["message"])
        calls.append(UUID(json.loads(message.value.decode("utf-8"))["task_id"]))
        return True

    monkeypatch.setattr(projector, "project_message", fake_project_message)

    projected = projector.project_polled_messages(
        consumer=cast(projector.ProjectorConsumer, consumer),
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(projector.ProjectorRedis, FakeRedis()),
        consumer_name="projector",
        projector_name="projector",
        poll_timeout_ms=250,
        max_records=10,
        task_result_ttl_seconds=300,
    )

    assert projected == 1
    assert consumer.commit_calls == 1
    assert calls == [task_id]


def test_build_redpanda_consumer_uses_projector_group_and_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeKafkaConsumer(FakeConsumer):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(projector, "KafkaConsumer", FakeKafkaConsumer)

    consumer = projector.build_redpanda_consumer(
        SimpleNamespace(redpanda_bootstrap_servers="redpanda:9092")
    )

    typed_consumer = cast(FakeConsumer, consumer)
    assert captured["bootstrap_servers"] == ["redpanda:9092"]
    assert captured["group_id"] == "solution3-projector"
    assert typed_consumer.subscribed_topics == [
        "tasks.requested",
        "tasks.started",
        "tasks.completed",
        "tasks.failed",
        "tasks.cancelled",
        "tasks.expired",
    ]


def test_build_redpanda_consumer_rejects_empty_bootstrap_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeKafkaConsumer(FakeConsumer):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()

    monkeypatch.setattr(projector, "KafkaConsumer", FakeKafkaConsumer)

    with pytest.raises(RuntimeError, match="bootstrap servers are not configured"):
        projector.build_redpanda_consumer(
            SimpleNamespace(
                redpanda_bootstrap_servers=" , ",
                redpanda_topic_task_requested="tasks.requested",
                redpanda_topic_task_started="tasks.started",
                redpanda_topic_task_completed="tasks.completed",
                redpanda_topic_task_failed="tasks.failed",
                redpanda_topic_task_cancelled="tasks.cancelled",
                redpanda_topic_task_expired="tasks.expired",
            )
        )


@pytest.mark.asyncio
async def test_project_polled_messages_async_returns_zero_for_empty_poll() -> None:
    consumer = FakeConsumer(records={})

    projected = await projector.project_polled_messages_async(
        consumer=cast(projector.ProjectorConsumer, consumer),
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(projector.ProjectorRedis, FakeRedis()),
        consumer_name="projector",
        projector_name="projector",
        poll_timeout_ms=250,
        max_records=10,
        task_result_ttl_seconds=300,
    )

    assert projected == 0
    assert consumer.commit_calls == 0


@pytest.mark.asyncio
async def test_main_async_processes_batch_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    stop_event = asyncio.Event()
    consumer = FakeConsumer()

    class RuntimeRedis(FakeRedis):
        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            events.append(("redis_closed", {}))

    class FakePool:
        async def close(self) -> None:
            events.append(("pool_closed", {}))

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

        def exception(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_project_batch(*_: object, **__: object) -> int:
        stop_event.set()
        return 2

    monkeypatch.setattr(projector, "logger", FakeLogger())
    monkeypatch.setattr(
        projector,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.projector.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.projector.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr(projector, "build_redpanda_consumer", lambda _settings: consumer)
    monkeypatch.setattr("solution3.workers.projector.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(projector, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(projector, "project_polled_messages_async", fake_project_batch)

    await projector._main_async(
        interval_seconds=0.1,
        poll_timeout_ms=250,
        max_records=10,
        task_result_ttl_seconds=300,
    )

    assert (
        "projector_started",
        {"interval_seconds": 0.1, "poll_timeout_ms": 250, "max_records": 10},
    ) in events
    assert ("projector_batch_projected", {"count": 2}) in events
    assert ("redis_closed", {}) in events
    assert ("pool_closed", {}) in events
    assert consumer.closed is True


@pytest.mark.asyncio
async def test_main_async_logs_iteration_failure_then_waits_for_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    stop_event = asyncio.Event()
    consumer = FakeConsumer()
    wait_calls: list[float] = []

    class RuntimeRedis(FakeRedis):
        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            events.append("redis_closed")

    class FakePool:
        async def close(self) -> None:
            events.append("pool_closed")

    class FakeLogger:
        def info(self, event: str, **_: object) -> None:
            events.append(event)

        def exception(self, event: str, **_: object) -> None:
            events.append(event)

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_project_batch(*_: object, **__: object) -> int:
        raise RuntimeError("boom")

    async def fake_wait_for(awaitable: object, *, timeout: float) -> object:
        wait_calls.append(timeout)
        stop_event.set()
        return await cast(asyncio.Future[bool], awaitable)

    monkeypatch.setattr(projector, "logger", FakeLogger())
    monkeypatch.setattr(
        projector,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.projector.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.projector.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr(projector, "build_redpanda_consumer", lambda _settings: consumer)
    monkeypatch.setattr("solution3.workers.projector.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(projector, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(projector, "project_polled_messages_async", fake_project_batch)
    monkeypatch.setattr("solution3.workers.projector.asyncio.wait_for", fake_wait_for)

    await projector._main_async(
        interval_seconds=0.25,
        poll_timeout_ms=250,
        max_records=10,
        task_result_ttl_seconds=300,
    )

    assert "projector_iteration_failed" in events
    assert "projector_stopped" in events
    assert "redis_closed" in events
    assert "pool_closed" in events
    assert wait_calls == [0.25]


def test_projector_main_configures_logging_and_runs_async(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[float, int, int, int]] = []

    async def fake_main_async(
        *,
        interval_seconds: float,
        poll_timeout_ms: int,
        max_records: int,
        task_result_ttl_seconds: int,
    ) -> None:
        async_calls.append(
            (interval_seconds, poll_timeout_ms, max_records, task_result_ttl_seconds)
        )

    def fake_asyncio_run(coro: object) -> None:
        assert asyncio.iscoroutine(coro)
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(
        projector,
        "_parse_args",
        lambda: SimpleNamespace(
            interval=2.0,
            poll_timeout_ms=250,
            max_records=10,
            result_ttl_seconds=300,
        ),
    )
    monkeypatch.setattr(projector, "_main_async", fake_main_async)
    monkeypatch.setattr(
        projector,
        "configure_logging",
        lambda *, enable_sensitive: configure_calls.append(enable_sensitive),
    )
    monkeypatch.setattr("solution3.workers.projector.asyncio.run", fake_asyncio_run)

    projector.main()

    assert configure_calls == [False]
    assert async_calls == [(2.0, 250, 10, 300)]


def test_main_configures_logging_and_runs_async_main(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[float, int, int, int]] = []

    async def fake_main_async(
        *,
        interval_seconds: float,
        poll_timeout_ms: int,
        max_records: int,
        task_result_ttl_seconds: int,
    ) -> None:
        async_calls.append(
            (interval_seconds, poll_timeout_ms, max_records, task_result_ttl_seconds)
        )

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    def fake_asyncio_run(coro: object) -> None:
        assert hasattr(coro, "send")
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(
        projector,
        "_parse_args",
        lambda: SimpleNamespace(
            interval=2.5,
            poll_timeout_ms=250,
            max_records=25,
            result_ttl_seconds=600,
        ),
    )
    monkeypatch.setattr(projector, "_main_async", fake_main_async)
    monkeypatch.setattr(projector, "configure_logging", fake_configure_logging)
    monkeypatch.setattr("solution3.workers.projector.asyncio.run", fake_asyncio_run)

    projector.main()

    assert configure_calls == [False]
    assert async_calls == [(2.5, 250, 25, 600)]
