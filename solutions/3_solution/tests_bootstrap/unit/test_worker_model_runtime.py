from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from uuid6 import uuid7

from solution3.constants import BillingState, ModelClass, RequestMode, SubscriptionTier, TaskStatus
from solution3.models.domain import TaskCommand
from solution3.workers.worker import WorkerModelRuntime


class FakeWarmRedis:
    def __init__(self) -> None:
        self.members: list[tuple[str, str]] = []

    async def sadd(self, key: str, value: str) -> int:
        self.members.append((key, value))
        return 1


def _task_command(*, model_class: ModelClass) -> TaskCommand:
    now = datetime.now(tz=UTC)
    return TaskCommand(
        task_id=uuid7(),
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=model_class,
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
async def test_execute_cold_starts_then_registers_warm_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeWarmRedis()
    runtime = WorkerModelRuntime(worker_id="worker-a", redis_client=redis)
    task = _task_command(model_class=ModelClass.MEDIUM)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("solution3.workers.worker.asyncio.sleep", fake_sleep)

    result = await runtime.execute(task)

    assert result == {"sum": 5}
    assert runtime.warm_model == ModelClass.MEDIUM
    assert sleep_calls == [3.0, 4.0]
    assert redis.members == [("warm:medium", "worker-a")]


@pytest.mark.asyncio
async def test_execute_hot_path_skips_model_load(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeWarmRedis()
    runtime = WorkerModelRuntime(worker_id="worker-a", redis_client=redis)
    runtime.warm_model = ModelClass.SMALL
    task = _task_command(model_class=ModelClass.SMALL)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("solution3.workers.worker.asyncio.sleep", fake_sleep)

    result = await runtime.execute(task)

    assert result == {"sum": 5}
    assert sleep_calls == [2.0]
    assert redis.members == []


@pytest.mark.asyncio
async def test_go_warm_registers_worker_in_model_registry() -> None:
    redis = FakeWarmRedis()
    runtime = WorkerModelRuntime(worker_id="worker-b", redis_client=redis)

    await runtime.go_warm(ModelClass.LARGE)

    assert runtime.warm_model == ModelClass.LARGE
    assert redis.members == [("warm:large", "worker-b")]


@pytest.mark.asyncio
async def test_execute_switching_models_triggers_second_cold_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeWarmRedis()
    runtime = WorkerModelRuntime(worker_id="worker-c", redis_client=redis)
    small_task = _task_command(model_class=ModelClass.SMALL)
    large_task = _task_command(model_class=ModelClass.LARGE)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("solution3.workers.worker.asyncio.sleep", fake_sleep)

    first_result = await runtime.execute(small_task)
    second_result = await runtime.execute(large_task)

    assert first_result == {"sum": 5}
    assert second_result == {"sum": 5}
    assert runtime.warm_model == ModelClass.LARGE
    assert sleep_calls == [3.0, 2.0, 3.0, 6.0]
    assert redis.members == [("warm:small", "worker-c"), ("warm:large", "worker-c")]


@pytest.mark.asyncio
async def test_execute_without_warm_registry_still_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = WorkerModelRuntime(worker_id="worker-d")
    task = _task_command(model_class=ModelClass.SMALL)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("solution3.workers.worker.asyncio.sleep", fake_sleep)

    result = await runtime.execute(task)

    assert result == {"sum": 5}
    assert runtime.warm_model == ModelClass.SMALL
    assert sleep_calls == [3.0, 2.0]
