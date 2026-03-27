from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from solution3.workers import rebuilder


class FakeConsumer:
    def __init__(self, records: list[dict[object, list[object]]] | None = None) -> None:
        self.records = records or []
        self.commit_calls = 0
        self.closed = False

    def poll(self, *, timeout_ms: int, max_records: int) -> dict[object, list[object]]:
        if self.records:
            return self.records.pop(0)
        return {}

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, task_keys: list[str] | None = None) -> None:
        self.task_keys = task_keys or []
        self.deleted_batches: list[tuple[str, ...]] = []
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def delete(self, *keys: str) -> int:
        self.deleted_batches.append(keys)
        return len(keys)

    async def close(self) -> None:
        self.closed = True

    async def scan_iter(self, *, match: str) -> AsyncIterator[str]:
        assert match == "task:*"
        for key in self.task_keys:
            yield key


@pytest.mark.asyncio
async def test_clear_task_cache_removes_only_task_keys() -> None:
    redis = FakeRedis(task_keys=["task:1", "task:2"])

    deleted = await rebuilder.clear_task_cache(cast(rebuilder.RebuilderRedis, redis))

    assert deleted == 2
    assert redis.deleted_batches == [("task:1", "task:2")]


@pytest.mark.asyncio
async def test_clear_task_cache_is_noop_without_redis() -> None:
    deleted = await rebuilder.clear_task_cache(None)

    assert deleted == 0


@pytest.mark.asyncio
async def test_rebuild_from_sql_resets_projection_state_and_rebuilds_from_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis(task_keys=["task:abc"])
    calls: list[tuple[str, object]] = []

    async def fake_reset(
        _pool: asyncpg.Pool,
        *,
        consumer_names: tuple[str, ...],
        projector_names: tuple[str, ...],
    ) -> None:
        calls.append(("reset", (consumer_names, projector_names)))

    async def fake_rebuild(_pool: asyncpg.Pool) -> int:
        calls.append(("sql", None))
        return 3

    monkeypatch.setattr(rebuilder, "reset_projection_state", fake_reset)
    monkeypatch.setattr(rebuilder, "rebuild_task_query_view_from_commands", fake_rebuild)

    result = await rebuilder.rebuild_from_sql(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(rebuilder.RebuilderRedis, redis),
    )

    assert result == rebuilder.RebuildResult(records_processed=3, cache_keys_deleted=1)
    assert calls == [
        ("reset", (("projector", "projector-rebuild"), ("projector", "projector-rebuild"))),
        ("sql", None),
    ]


@pytest.mark.asyncio
async def test_rebuild_from_events_replays_until_empty_poll_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = SimpleNamespace(
        topic="tasks.completed",
        partition=0,
        offset=5,
        value=b'{"task_id":"019c6db7-0857-7858-af93-f724ae4fe2c2"}',
        headers=[("event_id", str(uuid4()).encode("utf-8"))],
    )
    consumer = FakeConsumer(records=[{object(): [message]}, {}, {}])
    redis = FakeRedis(task_keys=["task:stale"])
    calls: list[int] = []

    async def fake_reset(
        _pool: asyncpg.Pool,
        *,
        consumer_names: tuple[str, ...],
        projector_names: tuple[str, ...],
    ) -> None:
        calls.append(len(consumer_names) + len(projector_names))

    async def fake_project_message(**kwargs: object) -> bool:
        message = cast(SimpleNamespace, kwargs["message"])
        calls.append(cast(int, message.offset))
        return True

    monkeypatch.setattr(rebuilder, "reset_projection_state", fake_reset)
    monkeypatch.setattr(rebuilder, "project_message", fake_project_message)

    result = await rebuilder.rebuild_from_events(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(rebuilder.RebuilderRedis, redis),
        consumer=cast(Any, consumer),
        poll_timeout_ms=25,
        max_records=10,
        task_result_ttl_seconds=300,
        max_empty_polls=2,
    )

    assert result == rebuilder.RebuildResult(records_processed=1, cache_keys_deleted=1)
    assert consumer.commit_calls == 1
    assert consumer.closed is False
    assert calls == [4, 5]


def test_main_uses_event_rebuild_when_from_beginning(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[bool, int]] = []

    async def fake_main_async(
        *,
        from_beginning: bool,
        max_empty_polls: int,
        **_kwargs: object,
    ) -> None:
        async_calls.append((from_beginning, max_empty_polls))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    def fake_asyncio_run(coro: object) -> None:
        assert hasattr(coro, "send")
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(
        rebuilder,
        "_parse_args",
        lambda: SimpleNamespace(from_beginning=True, max_empty_polls=4),
    )
    monkeypatch.setattr(rebuilder, "_main_async", fake_main_async)
    monkeypatch.setattr(rebuilder, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)

    rebuilder.main()

    assert configure_calls == [False]
    assert async_calls == [(True, 4)]


@pytest.mark.asyncio
async def test_main_async_uses_sql_strategy_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class FakePool:
        async def close(self) -> None:
            events.append(("pool_closed", {}))

    class RuntimeRedis(FakeRedis):
        async def close(self) -> None:
            events.append(("redis_closed", {}))

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_rebuild_from_sql(*_: object, **__: object) -> rebuilder.RebuildResult:
        return rebuilder.RebuildResult(records_processed=3, cache_keys_deleted=1)

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

    monkeypatch.setattr(rebuilder, "logger", FakeLogger())
    monkeypatch.setattr(
        rebuilder,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.rebuilder.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.rebuilder.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr(rebuilder, "rebuild_from_sql", fake_rebuild_from_sql)

    await rebuilder._main_async(
        from_beginning=False,
        max_empty_polls=2,
        poll_timeout_ms=250,
        max_records=10,
        task_result_ttl_seconds=300,
    )

    assert (
        "projection_rebuild_completed",
        {"strategy": "sql", "records_processed": 3, "cache_keys_deleted": 1},
    ) in events
    assert ("redis_closed", {}) in events
    assert ("pool_closed", {}) in events


@pytest.mark.asyncio
async def test_main_async_uses_event_strategy_and_closes_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    class FakePool:
        async def close(self) -> None:
            events.append(("pool_closed", {}))

    class RuntimeRedis(FakeRedis):
        async def close(self) -> None:
            events.append(("redis_closed", {}))

    consumer = FakeConsumer()

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_rebuild_from_events(*_: object, **__: object) -> rebuilder.RebuildResult:
        return rebuilder.RebuildResult(records_processed=5, cache_keys_deleted=2)

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

    monkeypatch.setattr(rebuilder, "logger", FakeLogger())
    monkeypatch.setattr(
        rebuilder,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.rebuilder.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.rebuilder.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr(rebuilder, "build_redpanda_consumer", lambda *_args, **_kwargs: consumer)
    monkeypatch.setattr(rebuilder, "rebuild_from_events", fake_rebuild_from_events)
    monkeypatch.setattr(rebuilder, "uuid4", lambda: "rebuild-run")

    await rebuilder._main_async(
        from_beginning=True,
        max_empty_polls=2,
        poll_timeout_ms=250,
        max_records=10,
        task_result_ttl_seconds=300,
    )

    assert (
        "projection_rebuild_completed",
        {"strategy": "events", "records_processed": 5, "cache_keys_deleted": 2},
    ) in events
    assert consumer.closed is True
    assert ("redis_closed", {}) in events
    assert ("pool_closed", {}) in events
