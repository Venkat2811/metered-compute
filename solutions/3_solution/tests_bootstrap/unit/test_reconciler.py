from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from solution3.constants import BillingState, ModelClass, RequestMode, SubscriptionTier, TaskStatus
from solution3.models.domain import ReconciledTaskState, StaleReservedTask
from solution3.workers import reconciler


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}
        self.decrements: list[str] = []
        self.set_calls: list[tuple[str, int]] = []

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes[key] = {**self.hashes.get(key, {}), **mapping}

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def decr(self, key: str) -> int:
        self.decrements.append(key)
        return 0

    async def set(self, key: str, value: int) -> None:
        self.set_calls.append((key, value))


@dataclass(frozen=True, slots=True)
class _FakeState:
    task_id: UUID
    user_id: UUID
    status: TaskStatus
    billing_state: BillingState
    model_class: ModelClass


def _stale_task() -> StaleReservedTask:
    return StaleReservedTask(
        task_id=uuid4(),
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
        tb_pending_transfer_id=uuid4(),
        created_at=datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_reconcile_stale_reserved_tasks_expires_rows_and_updates_hot_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    redis = FakeRedis()
    expired_state = ReconciledTaskState(
        task_id=stale_task.task_id,
        user_id=stale_task.user_id,
        status=TaskStatus.EXPIRED,
        billing_state=BillingState.EXPIRED,
        model_class=stale_task.model_class,
    )

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    async def fake_expire(*_args: object, **kwargs: object) -> ReconciledTaskState | None:
        assert kwargs["task_id"] == stale_task.task_id
        assert kwargs["tb_pending_transfer_id"] == stale_task.tb_pending_transfer_id
        return expired_state

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "expire_stale_reserved_task", fake_expire)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(reconciler.ReconcilerRedis, redis),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 1
    assert redis.decrements == [f"active:{stale_task.user_id}"]
    assert redis.hashes[f"task:{stale_task.task_id}"]["status"] == "EXPIRED"
    assert redis.hashes[f"task:{stale_task.task_id}"]["billing_state"] == "EXPIRED"
    assert redis.expirations[f"task:{stale_task.task_id}"] == 300


@pytest.mark.asyncio
async def test_reconcile_skips_rows_that_lose_the_update_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    redis = FakeRedis()

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    async def fake_expire(*_args: object, **_kwargs: object) -> ReconciledTaskState | None:
        return None

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "expire_stale_reserved_task", fake_expire)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(reconciler.ReconcilerRedis, redis),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 0
    assert redis.hashes == {}


def test_main_uses_single_run_when_once_is_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[bool, int]] = []

    async def fake_main_async(
        *,
        once: bool,
        stale_after_seconds: int,
        **_kwargs: object,
    ) -> None:
        async_calls.append((once, stale_after_seconds))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    def fake_asyncio_run(coro: object) -> None:
        assert hasattr(coro, "send")
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(
        reconciler,
        "_parse_args",
        lambda: SimpleNamespace(
            once=True, interval=30.0, stale_after_seconds=720, result_ttl_seconds=300
        ),
    )
    monkeypatch.setattr(reconciler, "_main_async", fake_main_async)
    monkeypatch.setattr(reconciler, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)

    reconciler.main()

    assert configure_calls == [False]
    assert async_calls == [(True, 720)]


@pytest.mark.asyncio
async def test_decrement_active_counter_clamps_negative_values() -> None:
    class NegativeRedis(FakeRedis):
        async def decr(self, key: str) -> int:
            self.decrements.append(key)
            return -1

    redis = NegativeRedis()

    await reconciler._decrement_active_counter(redis, user_id="user-1")

    assert redis.decrements == ["active:user-1"]
    assert redis.set_calls == [("active:user-1", 0)]


@pytest.mark.asyncio
async def test_cache_reconciled_state_is_noop_without_redis() -> None:
    await reconciler._cache_reconciled_state(
        redis_client=None,
        state=ReconciledTaskState(
            task_id=uuid4(),
            user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            status=TaskStatus.EXPIRED,
            billing_state=BillingState.EXPIRED,
            model_class=ModelClass.SMALL,
        ),
        result_ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_main_async_logs_resolution_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    stop_event = asyncio.Event()

    class FakePool:
        async def close(self) -> None:
            events.append(("pool_closed", {}))

    class RuntimeRedis(FakeRedis):
        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            events.append(("redis_closed", {}))

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_reconcile(*_: object, **__: object) -> int:
        stop_event.set()
        return 2

    monkeypatch.setattr(reconciler, "logger", FakeLogger())
    monkeypatch.setattr(
        reconciler,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.reconciler.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(reconciler, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(reconciler, "reconcile_stale_reserved_tasks", fake_reconcile)

    await reconciler._main_async(
        once=False,
        stale_after_seconds=720,
        interval_seconds=0.1,
        result_ttl_seconds=300,
    )

    assert ("reconciler_stale_resolved", {"count": 2}) in events
    assert ("redis_closed", {}) in events
    assert ("pool_closed", {}) in events


@pytest.mark.asyncio
async def test_main_async_waits_between_iterations_until_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wait_calls: list[float] = []
    stop_event = asyncio.Event()

    class FakePool:
        async def close(self) -> None:
            return None

    class RuntimeRedis(FakeRedis):
        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            return None

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_reconcile(*_: object, **__: object) -> int:
        return 0

    async def fake_wait_for(awaitable: Awaitable[bool], *, timeout: float) -> bool:
        wait_calls.append(timeout)
        stop_event.set()
        return await awaitable

    monkeypatch.setattr(
        reconciler,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.reconciler.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(reconciler, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(reconciler, "reconcile_stale_reserved_tasks", fake_reconcile)
    monkeypatch.setattr("solution3.workers.reconciler.asyncio.wait_for", fake_wait_for)

    await reconciler._main_async(
        once=False,
        stale_after_seconds=720,
        interval_seconds=0.25,
        result_ttl_seconds=300,
    )

    assert wait_calls == [0.25]
