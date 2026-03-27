from __future__ import annotations

import asyncio
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
