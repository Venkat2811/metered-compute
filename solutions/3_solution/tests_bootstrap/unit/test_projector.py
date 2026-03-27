from __future__ import annotations

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
    ]


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
