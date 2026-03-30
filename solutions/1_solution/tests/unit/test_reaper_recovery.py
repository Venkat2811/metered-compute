from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

import solution1.workers.reaper as reaper_module
from solution1.constants import TaskStatus
from solution1.core.defaults import DEFAULT_ALICE_API_KEY
from solution1.models.domain import TaskRecord
from tests.constants import TASK_ID_PRIMARY, TEST_USER_ID


class _FakeTxContext:
    async def __aenter__(self) -> _FakeTxContext:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConnection:
    def transaction(self) -> _FakeTxContext:
        return _FakeTxContext()


class _FakeAcquireContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self._connection = _FakeConnection()

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self._connection)


@pytest.mark.asyncio
async def test_process_stuck_tasks_refunds_once(monkeypatch: pytest.MonkeyPatch) -> None:
    task = TaskRecord(
        task_id=TASK_ID_PRIMARY,
        api_key=DEFAULT_ALICE_API_KEY,
        user_id=TEST_USER_ID,
        x=1,
        y=2,
        cost=10,
        status=TaskStatus.RUNNING,
        result=None,
        error=None,
        runtime_ms=None,
        idempotency_key=None,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        completed_at=None,
    )

    async def fake_list_stuck_running_tasks(*_: object, **__: object) -> list[TaskRecord]:
        return [task]

    failed_calls: list[UUID] = []
    credit_calls: list[tuple[UUID, int, str]] = []
    refund_calls: list[tuple[UUID, int]] = []

    async def fake_update_task_failed(*_: object, **kwargs: object) -> bool:
        failed_calls.append(cast(UUID, kwargs["task_id"]))
        return True

    async def fake_insert_credit_transaction(*_: object, **kwargs: object) -> None:
        credit_calls.append(
            (
                cast(UUID, kwargs["user_id"]),
                cast(int, kwargs["delta"]),
                str(kwargs["reason"]),
            )
        )

    async def fake_refund_and_decrement_active(**kwargs: object) -> str:
        refund_calls.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "decr-sha-updated"

    monkeypatch.setattr(reaper_module, "list_stuck_running_tasks", fake_list_stuck_running_tasks)
    monkeypatch.setattr(reaper_module, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(reaper_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        reaper_module, "refund_and_decrement_active", fake_refund_and_decrement_active
    )

    recovered, script_sha = await reaper_module._process_stuck_tasks(
        pool=cast(Any, _FakePool()),
        redis_client=cast(Any, object()),
        decrement_script_sha="decr-sha-initial",
        stuck_timeout_seconds=1,
        retry_attempts=3,
        retry_base_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
    )

    assert recovered == 1
    assert script_sha == "decr-sha-updated"
    assert failed_calls == [task.task_id]
    assert credit_calls == [(task.user_id, task.cost, "stuck_refund")]
    assert refund_calls == [(task.user_id, task.cost)]


@pytest.mark.asyncio
async def test_process_stuck_tasks_skips_refund_when_task_not_transitioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = TaskRecord(
        task_id=TASK_ID_PRIMARY,
        api_key=DEFAULT_ALICE_API_KEY,
        user_id=TEST_USER_ID,
        x=1,
        y=2,
        cost=10,
        status=TaskStatus.RUNNING,
        result=None,
        error=None,
        runtime_ms=None,
        idempotency_key=None,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        completed_at=None,
    )

    async def fake_list_stuck_running_tasks(*_: object, **__: object) -> list[TaskRecord]:
        return [task]

    failed_calls: list[UUID] = []
    credit_calls: list[tuple[UUID, int, str]] = []
    refund_calls: list[tuple[UUID, int]] = []

    async def fake_update_task_failed(*_: object, **kwargs: object) -> bool:
        failed_calls.append(cast(UUID, kwargs["task_id"]))
        return False

    async def fake_insert_credit_transaction(*_: object, **kwargs: object) -> None:
        credit_calls.append(
            (
                cast(UUID, kwargs["user_id"]),
                cast(int, kwargs["delta"]),
                str(kwargs["reason"]),
            )
        )

    async def fake_refund_and_decrement_active(**kwargs: object) -> str:
        refund_calls.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "decr-sha-updated"

    monkeypatch.setattr(reaper_module, "list_stuck_running_tasks", fake_list_stuck_running_tasks)
    monkeypatch.setattr(reaper_module, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(reaper_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        reaper_module, "refund_and_decrement_active", fake_refund_and_decrement_active
    )

    recovered, script_sha = await reaper_module._process_stuck_tasks(
        pool=cast(Any, _FakePool()),
        redis_client=cast(Any, object()),
        decrement_script_sha="decr-sha-initial",
        stuck_timeout_seconds=1,
        retry_attempts=3,
        retry_base_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
    )

    assert recovered == 0
    assert script_sha == "decr-sha-initial"
    assert failed_calls == [task.task_id]
    assert credit_calls == []
    assert refund_calls == []


@pytest.mark.asyncio
async def test_process_stuck_tasks_skips_refund_when_credit_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = TaskRecord(
        task_id=TASK_ID_PRIMARY,
        api_key=DEFAULT_ALICE_API_KEY,
        user_id=TEST_USER_ID,
        x=1,
        y=2,
        cost=10,
        status=TaskStatus.RUNNING,
        result=None,
        error=None,
        runtime_ms=None,
        idempotency_key=None,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        completed_at=None,
    )

    async def fake_list_stuck_running_tasks(*_: object, **__: object) -> list[TaskRecord]:
        return [task]

    failed_calls: list[UUID] = []
    credit_calls: list[tuple[UUID, int, str]] = []
    refund_calls: list[tuple[UUID, int]] = []

    async def fake_update_task_failed(*_: object, **kwargs: object) -> bool:
        failed_calls.append(cast(UUID, kwargs["task_id"]))
        return True

    async def fake_insert_credit_transaction(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("pg write failed")

    async def fake_refund_and_decrement_active(**kwargs: object) -> str:
        refund_calls.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "decr-sha-updated"

    monkeypatch.setattr(reaper_module, "list_stuck_running_tasks", fake_list_stuck_running_tasks)
    monkeypatch.setattr(reaper_module, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(reaper_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        reaper_module, "refund_and_decrement_active", fake_refund_and_decrement_active
    )

    recovered, script_sha = await reaper_module._process_stuck_tasks(
        pool=cast(Any, _FakePool()),
        redis_client=cast(Any, object()),
        decrement_script_sha="decr-sha-initial",
        stuck_timeout_seconds=1,
        retry_attempts=3,
        retry_base_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
    )

    assert recovered == 0
    assert script_sha == "decr-sha-initial"
    assert failed_calls == [task.task_id]
    assert credit_calls == []
    assert refund_calls == []
