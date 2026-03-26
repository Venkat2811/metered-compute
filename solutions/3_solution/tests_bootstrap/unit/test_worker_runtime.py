from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import asyncpg
import pytest
from uuid6 import uuid7

from solution3.constants import BillingState, ModelClass, RequestMode, SubscriptionTier, TaskStatus
from solution3.models.domain import TaskCommand
from solution3.workers import worker


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}
        self.decrements: list[str] = []

    async def decr(self, key: str) -> int:
        self.decrements.append(key)
        return 0

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None:
        self.hashes[key] = {**self.hashes.get(key, {}), **dict(mapping)}

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


class FakeBilling:
    def __init__(self, *, post_ok: bool = True, void_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.void_ok = void_ok
        self.post_calls: list[UUID | str] = []
        self.void_calls: list[UUID | str] = []

    def post_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        self.post_calls.append(pending_transfer_id)
        return self.post_ok

    def void_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        self.void_calls.append(pending_transfer_id)
        return self.void_ok


def _task_command() -> TaskCommand:
    now = datetime.now(tz=UTC)
    return TaskCommand(
        task_id=uuid7(),
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
        x=2,
        y=3,
        cost=10,
        tb_pending_transfer_id=uuid7(),
        callback_url=None,
        idempotency_key="idem-1",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_mark_task_running_updates_db_and_hot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task_command()
    redis = FakeRedis()
    db_calls: list[UUID] = []

    async def fake_update_task_running(_pool: object, *, task_id: UUID) -> bool:
        db_calls.append(task_id)
        return True

    monkeypatch.setattr(worker, "update_task_running", fake_update_task_running)

    updated = await worker.mark_task_running(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        task=task,
        result_ttl_seconds=300,
    )

    assert updated is True
    assert db_calls == [task.task_id]
    assert redis.hashes[f"task:{task.task_id}"]["status"] == "RUNNING"
    assert redis.expirations[f"task:{task.task_id}"] == 300


@pytest.mark.asyncio
async def test_mark_task_running_skips_cache_when_db_guard_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()

    async def fake_update_task_running(_pool: object, *, task_id: UUID) -> bool:
        _ = task_id
        return False

    monkeypatch.setattr(worker, "update_task_running", fake_update_task_running)

    updated = await worker.mark_task_running(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        task=task,
        result_ttl_seconds=300,
    )

    assert updated is False
    assert redis.hashes == {}


@pytest.mark.asyncio
async def test_handle_successful_completion_posts_tb_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()
    billing = FakeBilling()
    finalized_calls: list[dict[str, object]] = []

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        finalized_calls.append(kwargs)
        return True

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=600,
        result={"sum": 5},
    )

    assert completed is True
    assert billing.post_calls == [task.tb_pending_transfer_id]
    assert billing.void_calls == []
    assert finalized_calls[0]["status"] == TaskStatus.COMPLETED
    assert finalized_calls[0]["billing_state"] == BillingState.CAPTURED
    assert redis.decrements == [f"active:{task.user_id}"]
    assert redis.hashes[f"task:{task.task_id}"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_handle_failed_completion_voids_tb_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()
    billing = FakeBilling()
    finalized_calls: list[dict[str, object]] = []

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        finalized_calls.append(kwargs)
        return True

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        billing=billing,
        task=task,
        success=False,
        result_ttl_seconds=600,
        error="boom",
    )

    assert completed is True
    assert billing.post_calls == []
    assert billing.void_calls == [task.tb_pending_transfer_id]
    assert finalized_calls[0]["status"] == TaskStatus.FAILED
    assert finalized_calls[0]["billing_state"] == BillingState.RELEASED
    assert redis.hashes[f"task:{task.task_id}"]["status"] == "FAILED"
    assert redis.hashes[f"task:{task.task_id}"]["error"] == "boom"


@pytest.mark.asyncio
async def test_handle_completion_stops_when_tb_operation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    billing = FakeBilling(post_ok=False)
    finalized = False

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        nonlocal finalized
        _ = kwargs
        finalized = True
        return True

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, FakeRedis()),
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=600,
        result={"sum": 5},
    )

    assert completed is False
    assert finalized is False


@pytest.mark.asyncio
async def test_handle_completion_stops_when_terminal_update_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()
    billing = FakeBilling()

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        _ = kwargs
        return False

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=600,
        result={"sum": 5},
    )

    assert completed is False
    assert redis.hashes == {}
