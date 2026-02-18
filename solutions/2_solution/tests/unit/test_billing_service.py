from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from uuid6 import uuid7

import solution2.services.billing as billing
from solution2.constants import (
    ModelClass,
    RequestMode,
    ReservationState,
    SubscriptionTier,
    TaskStatus,
)
from solution2.models.domain import CreditReservation
from solution2.services.billing import (
    AdmissionDecision,
    BatchAdmissionResult,
    BatchTaskSpec,
    run_admission_gate,
    run_batch_admission_gate,
    run_sync_submission,
)
from tests.constants import TEST_USER_ID
from tests.fakes import FakePool


class _FakeGauge:
    def __init__(self) -> None:
        self.inc_calls = 0

    def inc(self, amount: float = 1.0) -> None:
        self.inc_calls += int(amount)


def _reservation(*, task_id: UUID, user_id: UUID, amount: int = 10) -> CreditReservation:
    now = datetime.now(tz=UTC)
    return CreditReservation(
        reservation_id=uuid4(),
        task_id=task_id,
        user_id=user_id,
        amount=amount,
        state=ReservationState.RESERVED,
        expires_at=now + timedelta(minutes=5),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_run_admission_gate_requires_db_pool() -> None:
    with pytest.raises(ValueError, match="db_pool is required"):
        await run_admission_gate(
            admission_script_sha="sha",
            user_id=uuid4(),
            task_id=uuid4(),
            cost=10,
            idempotency_value="idem",
            max_concurrent=3,
            stream_payload={
                "x": 1,
                "y": 2,
                "tier": SubscriptionTier.PRO.value,
                "mode": RequestMode.ASYNC.value,
                "model_class": ModelClass.SMALL.value,
            },
        )


@pytest.mark.asyncio
async def test_run_admission_gate_db_path_rejects_insufficient_credits_without_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid7()
    user_id = uuid4()
    db_calls: list[str] = []
    fake_pool = FakePool()
    gauge = _FakeGauge()

    async def no_duplicate(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("write path should not run when credits are insufficient")

    async def fake_get_task_command_by_idempotency(
        *_args: object,
        **_kwargs: object,
    ) -> None:
        db_calls.append("get_task_command_by_idempotency")
        return None

    async def fake_lock_user_for_admission(
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        db_calls.append("lock_user_for_admission")
        return True

    async def fake_count_active_reservations(
        *_args: object,
        **_kwargs: object,
    ) -> int:
        db_calls.append("count_active_reservations")
        return 0

    async def fake_reserve_user_credits(
        *_args: object,
        **_kwargs: object,
    ) -> None:
        db_calls.append("reserve_user_credits")
        return None

    monkeypatch.setattr(
        "solution2.services.billing.get_task_command_by_idempotency",
        fake_get_task_command_by_idempotency,
    )
    monkeypatch.setattr(
        "solution2.services.billing.lock_user_for_admission",
        fake_lock_user_for_admission,
    )
    monkeypatch.setattr(
        "solution2.services.billing.count_active_reservations",
        fake_count_active_reservations,
    )
    monkeypatch.setattr(
        "solution2.services.billing.reserve_user_credits",
        fake_reserve_user_credits,
    )
    monkeypatch.setattr("solution2.services.billing.RESERVATIONS_ACTIVE_GAUGE", gauge)
    monkeypatch.setattr(
        "solution2.services.billing.create_task_command",
        no_duplicate,
    )
    monkeypatch.setattr("solution2.services.billing.create_reservation", no_duplicate)
    monkeypatch.setattr("solution2.services.billing.create_outbox_event", no_duplicate)

    decision, sha = await run_admission_gate(
        admission_script_sha="sha",
        user_id=user_id,
        task_id=task_id,
        cost=5,
        idempotency_value="idem-1",
        max_concurrent=3,
        db_pool=fake_pool,
        stream_payload={
            "x": 1,
            "y": 2,
            "tier": SubscriptionTier.PRO.value,
            "mode": RequestMode.ASYNC.value,
            "model_class": ModelClass.SMALL.value,
        },
        reservation_ttl_seconds=1200,
    )
    assert decision == AdmissionDecision(ok=False, reason="INSUFFICIENT", existing_task_id=None)
    assert sha == "sha"
    assert db_calls == [
        "lock_user_for_admission",
        "get_task_command_by_idempotency",
        "count_active_reservations",
        "reserve_user_credits",
    ]
    assert gauge.inc_calls == 0


@pytest.mark.asyncio
async def test_run_admission_gate_db_path_completes_reservation_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid7()
    user_id = TEST_USER_ID
    db_calls: list[str] = []
    fake_pool = FakePool()
    gauge = _FakeGauge()

    async def fake_get_task_command_by_idempotency(*_args: object, **_kwargs: object) -> None:
        db_calls.append("get_task_command_by_idempotency")
        return None

    async def fake_lock_user_for_admission(
        *_args: object,
        **_kwargs: object,
    ) -> bool:
        db_calls.append("lock_user_for_admission")
        return True

    async def fake_count_active_reservations(
        *_args: object,
        **_kwargs: object,
    ) -> int:
        db_calls.append("count_active_reservations")
        return 0

    async def fake_reserve_user_credits(
        *_args: object,
        **_kwargs: object,
    ) -> int:
        db_calls.append("reserve_user_credits")
        return 95

    async def fake_create_task_command(*_args: object, **_kwargs: object) -> None:
        db_calls.append("create_task_command")

    async def fake_create_reservation(*_args: object, **_kwargs: object) -> None:
        db_calls.append("create_reservation")

    async def fake_create_outbox_event(*_args: object, **_kwargs: object) -> str:
        db_calls.append("create_outbox_event")
        return "event-id"

    monkeypatch.setattr(
        "solution2.services.billing.get_task_command_by_idempotency",
        fake_get_task_command_by_idempotency,
    )
    monkeypatch.setattr(
        "solution2.services.billing.lock_user_for_admission",
        fake_lock_user_for_admission,
    )
    monkeypatch.setattr(
        "solution2.services.billing.count_active_reservations",
        fake_count_active_reservations,
    )
    monkeypatch.setattr(
        "solution2.services.billing.reserve_user_credits",
        fake_reserve_user_credits,
    )
    monkeypatch.setattr("solution2.services.billing.create_task_command", fake_create_task_command)
    monkeypatch.setattr("solution2.services.billing.create_reservation", fake_create_reservation)
    monkeypatch.setattr("solution2.services.billing.create_outbox_event", fake_create_outbox_event)
    monkeypatch.setattr("solution2.services.billing.RESERVATIONS_ACTIVE_GAUGE", gauge)
    decision, sha = await run_admission_gate(
        admission_script_sha="sha",
        user_id=user_id,
        task_id=task_id,
        cost=5,
        idempotency_value="idem-1",
        max_concurrent=3,
        db_pool=fake_pool,
        stream_payload={
            "x": 1,
            "y": 2,
            "tier": SubscriptionTier.PRO.value,
            "mode": RequestMode.ASYNC.value,
            "model_class": ModelClass.SMALL.value,
        },
        reservation_ttl_seconds=1200,
    )

    assert decision == AdmissionDecision(ok=True, reason="OK", existing_task_id=None)
    assert sha == ""
    assert "create_task_command" in db_calls
    assert "create_reservation" in db_calls
    assert "create_outbox_event" in db_calls
    assert gauge.inc_calls == 1


@pytest.mark.asyncio
async def test_run_admission_gate_maps_lock_contention_to_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()

    async def fake_execute(*_args: object, **_kwargs: object) -> AdmissionDecision:
        raise asyncpg.LockNotAvailableError("lock busy")

    monkeypatch.setattr("solution2.services.billing._execute_admission_transaction", fake_execute)

    decision, sha = await run_admission_gate(
        admission_script_sha="sha",
        user_id=uuid4(),
        task_id=uuid7(),
        cost=5,
        idempotency_value="idem-lock",
        max_concurrent=3,
        db_pool=fake_pool,
        stream_payload={
            "x": 1,
            "y": 2,
            "tier": SubscriptionTier.FREE.value,
            "mode": RequestMode.ASYNC.value,
            "model_class": ModelClass.SMALL.value,
        },
    )

    assert decision == AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None)
    assert sha == "sha"


@pytest.mark.asyncio
async def test_run_batch_admission_gate_requires_db_pool() -> None:
    with pytest.raises(ValueError, match="db_pool is required"):
        await run_batch_admission_gate(
            admission_script_sha="sha",
            user_id=uuid4(),
            user_tier=SubscriptionTier.PRO,
            batch_id=uuid7(),
            tasks=(BatchTaskSpec(x=1, y=2, model_class=ModelClass.SMALL),),
            max_concurrent=3,
            base_task_cost=10,
        )


@pytest.mark.asyncio
async def test_run_batch_admission_gate_accepts_and_updates_gauge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()
    gauge = _FakeGauge()
    batch_task_ids = (uuid7(), uuid7())

    async def fake_execute(**kwargs: object) -> BatchAdmissionResult:
        tasks = kwargs["tasks"]
        assert isinstance(tasks, tuple)
        return BatchAdmissionResult(
            ok=True,
            reason="OK",
            task_ids=batch_task_ids,
            total_cost=30,
        )

    monkeypatch.setattr(
        "solution2.services.billing._execute_batch_admission_transaction",
        fake_execute,
    )
    monkeypatch.setattr("solution2.services.billing.RESERVATIONS_ACTIVE_GAUGE", gauge)

    result, sha = await run_batch_admission_gate(
        admission_script_sha="sha",
        user_id=TEST_USER_ID,
        user_tier=SubscriptionTier.PRO,
        batch_id=uuid7(),
        tasks=(
            BatchTaskSpec(x=1, y=2, model_class=ModelClass.SMALL),
            BatchTaskSpec(x=3, y=4, model_class=ModelClass.MEDIUM),
        ),
        max_concurrent=6,
        base_task_cost=10,
        db_pool=fake_pool,
    )

    assert result.ok is True
    assert result.task_ids == batch_task_ids
    assert result.total_cost == 30
    assert sha == ""
    assert gauge.inc_calls == 2


@pytest.mark.asyncio
async def test_run_sync_submission_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()
    task_id = uuid7()
    user_id = uuid4()

    async def fake_execute_sync_admission(**_: object) -> AdmissionDecision:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None)

    async def fake_mark_running(**_: object) -> bool:
        return True

    async def fake_finalize_success(**_: object) -> bool:
        return True

    monkeypatch.setattr(
        "solution2.services.billing._execute_sync_admission_transaction",
        fake_execute_sync_admission,
    )
    monkeypatch.setattr("solution2.services.billing._mark_sync_running", fake_mark_running)
    monkeypatch.setattr(
        "solution2.services.billing._finalize_sync_success",
        fake_finalize_success,
    )
    monkeypatch.setattr(
        "solution2.services.billing.runtime_seconds_for_model",
        lambda _model: 0.001,
    )

    decision, sha, sync_result = await run_sync_submission(
        admission_script_sha="sha",
        user_id=user_id,
        user_tier=SubscriptionTier.ENTERPRISE,
        task_id=task_id,
        x=4,
        y=5,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="idem-sync",
        max_concurrent=3,
        queue_name="queue.realtime",
        execution_timeout_seconds=1.0,
        db_pool=fake_pool,
    )

    assert decision == AdmissionDecision(ok=True, reason="OK", existing_task_id=None)
    assert sha == ""
    assert sync_result is not None
    assert sync_result.status == TaskStatus.COMPLETED
    assert sync_result.result == {"z": 9}
    assert sync_result.error is None
    assert sync_result.runtime_ms is not None


@pytest.mark.asyncio
async def test_run_sync_submission_timeout_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()

    async def fake_execute_sync_admission(**_: object) -> AdmissionDecision:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None)

    async def fake_mark_running(**_: object) -> bool:
        return True

    async def fake_finalize_failure(**_: object) -> bool:
        return True

    monkeypatch.setattr(
        "solution2.services.billing._execute_sync_admission_transaction",
        fake_execute_sync_admission,
    )
    monkeypatch.setattr("solution2.services.billing._mark_sync_running", fake_mark_running)
    monkeypatch.setattr(
        "solution2.services.billing._finalize_sync_failure",
        fake_finalize_failure,
    )
    monkeypatch.setattr(
        "solution2.services.billing.runtime_seconds_for_model",
        lambda _model: 1.0,
    )

    decision, sha, sync_result = await run_sync_submission(
        admission_script_sha="sha",
        user_id=uuid4(),
        user_tier=SubscriptionTier.ENTERPRISE,
        task_id=uuid7(),
        x=1,
        y=2,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="idem-sync-timeout",
        max_concurrent=3,
        queue_name="queue.realtime",
        execution_timeout_seconds=0.001,
        db_pool=fake_pool,
    )

    assert decision == AdmissionDecision(ok=True, reason="OK", existing_task_id=None)
    assert sha == ""
    assert sync_result is not None
    assert sync_result.status == TaskStatus.TIMEOUT
    assert sync_result.error == "sync_execution_timeout"


@pytest.mark.asyncio
async def test_run_batch_admission_gate_executes_db_transaction_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()
    created_task_commands: list[str] = []
    created_reservations: list[str] = []
    created_outbox_events: list[str] = []

    async def fake_lock_user(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_count_active(*_args: object, **_kwargs: object) -> int:
        return 0

    async def fake_reserve_credits(*_args: object, **_kwargs: object) -> int:
        return 100

    async def fake_create_task_command(*_args: object, **kwargs: object) -> None:
        created_task_commands.append(str(kwargs["task_id"]))

    async def fake_create_reservation(*_args: object, **kwargs: object) -> None:
        created_reservations.append(str(kwargs["task_id"]))

    async def fake_create_outbox_event(*_args: object, **kwargs: object) -> str:
        created_outbox_events.append(str(kwargs["aggregate_id"]))
        return "event-id"

    monkeypatch.setattr("solution2.services.billing.lock_user_for_admission", fake_lock_user)
    monkeypatch.setattr("solution2.services.billing.count_active_reservations", fake_count_active)
    monkeypatch.setattr("solution2.services.billing.reserve_user_credits", fake_reserve_credits)
    monkeypatch.setattr("solution2.services.billing.create_task_command", fake_create_task_command)
    monkeypatch.setattr("solution2.services.billing.create_reservation", fake_create_reservation)
    monkeypatch.setattr("solution2.services.billing.create_outbox_event", fake_create_outbox_event)

    result, _sha = await run_batch_admission_gate(
        admission_script_sha="sha",
        user_id=TEST_USER_ID,
        user_tier=SubscriptionTier.PRO,
        batch_id=uuid7(),
        tasks=(
            BatchTaskSpec(x=1, y=2, model_class=ModelClass.SMALL),
            BatchTaskSpec(x=3, y=4, model_class=ModelClass.LARGE),
        ),
        max_concurrent=10,
        base_task_cost=10,
        db_pool=fake_pool,
        trace_id="trace-123",
    )

    assert result.ok is True
    assert result.reason == "OK"
    assert len(result.task_ids) == 2
    assert result.total_cost == 60
    assert len(created_task_commands) == 2
    assert len(created_reservations) == 2
    assert len(created_outbox_events) == 2


@pytest.mark.asyncio
async def test_run_sync_submission_returns_error_when_running_mark_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()

    async def fake_execute_sync_admission(**_: object) -> AdmissionDecision:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None)

    async def fake_mark_running(**_: object) -> bool:
        return False

    monkeypatch.setattr(
        "solution2.services.billing._execute_sync_admission_transaction",
        fake_execute_sync_admission,
    )
    monkeypatch.setattr("solution2.services.billing._mark_sync_running", fake_mark_running)

    decision, sha, sync_result = await run_sync_submission(
        admission_script_sha="sha",
        user_id=uuid4(),
        user_tier=SubscriptionTier.ENTERPRISE,
        task_id=uuid7(),
        x=1,
        y=2,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="idem-sync-running",
        max_concurrent=3,
        queue_name="queue.realtime",
        execution_timeout_seconds=1.0,
        db_pool=fake_pool,
    )

    assert decision == AdmissionDecision(ok=False, reason="ERROR", existing_task_id=None)
    assert sha == ""
    assert sync_result is None


@pytest.mark.asyncio
async def test_run_sync_submission_propagates_non_ok_admission_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()

    async def fake_execute_sync_admission(**_: object) -> AdmissionDecision:
        return AdmissionDecision(ok=False, reason="INSUFFICIENT", existing_task_id=None)

    monkeypatch.setattr(
        "solution2.services.billing._execute_sync_admission_transaction",
        fake_execute_sync_admission,
    )

    decision, sha, sync_result = await run_sync_submission(
        admission_script_sha="sha",
        user_id=uuid4(),
        user_tier=SubscriptionTier.PRO,
        task_id=uuid7(),
        x=1,
        y=2,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="idem-sync-insufficient",
        max_concurrent=3,
        queue_name="queue.fast",
        execution_timeout_seconds=1.0,
        db_pool=fake_pool,
    )

    assert decision == AdmissionDecision(ok=False, reason="INSUFFICIENT", existing_task_id=None)
    assert sha == "sha"
    assert sync_result is None


@pytest.mark.asyncio
async def test_billing_helpers_cover_parse_and_queue_branches() -> None:
    assert billing._coerce_int("42") == 42
    assert billing._coerce_int("bad", default=7) == 7
    assert (
        billing._resolve_queue_name(
            resolved_tier=SubscriptionTier.PRO,
            resolved_mode=RequestMode.ASYNC,
            model_class=ModelClass.SMALL.value,
            queue_name="queue.override",
        )
        == "queue.override"
    )
    with pytest.raises(ValueError, match="invalid model_class"):
        billing._normalize_payload(
            stream_payload={"x": 1, "y": 2, "tier": SubscriptionTier.PRO.value},
            request_mode=RequestMode.ASYNC.value,
        )


@pytest.mark.asyncio
async def test_execute_sync_admission_transaction_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()
    calls: list[str] = []

    async def fake_lock(*_args: object, **_kwargs: object) -> bool:
        calls.append("lock")
        return True

    async def fake_get_task(*_args: object, **_kwargs: object) -> None:
        calls.append("idempotency")
        return None

    async def fake_count(*_args: object, **_kwargs: object) -> int:
        calls.append("count")
        return 0

    async def fake_reserve(*_args: object, **_kwargs: object) -> int:
        calls.append("reserve")
        return 20

    async def fake_create_task(*_args: object, **_kwargs: object) -> None:
        calls.append("create_task")

    async def fake_create_reservation(*_args: object, **_kwargs: object) -> None:
        calls.append("create_reservation")

    monkeypatch.setattr("solution2.services.billing.lock_user_for_admission", fake_lock)
    monkeypatch.setattr("solution2.services.billing.get_task_command_by_idempotency", fake_get_task)
    monkeypatch.setattr("solution2.services.billing.count_active_reservations", fake_count)
    monkeypatch.setattr("solution2.services.billing.reserve_user_credits", fake_reserve)
    monkeypatch.setattr("solution2.services.billing.create_task_command", fake_create_task)
    monkeypatch.setattr("solution2.services.billing.create_reservation", fake_create_reservation)

    decision = await billing._execute_sync_admission_transaction(
        db_pool=fake_pool,
        user_id=uuid4(),
        user_tier=SubscriptionTier.ENTERPRISE,
        task_id=uuid7(),
        x=2,
        y=3,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="sync-idem",
        max_concurrent=4,
        reservation_ttl_seconds=300,
    )

    assert decision == AdmissionDecision(ok=True, reason="OK", existing_task_id=None)
    assert calls == ["lock", "idempotency", "count", "reserve", "create_task", "create_reservation"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lock_ok", "existing", "active_count", "credit_result", "expected_reason"),
    [
        (False, None, 0, 10, "ERROR"),
        (True, SimpleNamespace(task_id=uuid7()), 0, 10, "IDEMPOTENT"),
        (True, None, 5, 10, "CONCURRENCY"),
        (True, None, 0, None, "INSUFFICIENT"),
    ],
)
async def test_execute_sync_admission_transaction_rejection_paths(
    monkeypatch: pytest.MonkeyPatch,
    lock_ok: bool,
    existing: object | None,
    active_count: int,
    credit_result: int | None,
    expected_reason: str,
) -> None:
    fake_pool = FakePool()

    async def fake_lock(*_args: object, **_kwargs: object) -> bool:
        return lock_ok

    async def fake_get_task(*_args: object, **_kwargs: object) -> object | None:
        return existing

    async def fake_count(*_args: object, **_kwargs: object) -> int:
        return active_count

    async def fake_reserve(*_args: object, **_kwargs: object) -> int | None:
        return credit_result

    async def should_not_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("write path must not run on rejection")

    monkeypatch.setattr("solution2.services.billing.lock_user_for_admission", fake_lock)
    monkeypatch.setattr("solution2.services.billing.get_task_command_by_idempotency", fake_get_task)
    monkeypatch.setattr("solution2.services.billing.count_active_reservations", fake_count)
    monkeypatch.setattr("solution2.services.billing.reserve_user_credits", fake_reserve)
    monkeypatch.setattr("solution2.services.billing.create_task_command", should_not_write)
    monkeypatch.setattr("solution2.services.billing.create_reservation", should_not_write)

    decision = await billing._execute_sync_admission_transaction(
        db_pool=fake_pool,
        user_id=uuid4(),
        user_tier=SubscriptionTier.ENTERPRISE,
        task_id=uuid7(),
        x=2,
        y=3,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="sync-idem",
        max_concurrent=4,
        reservation_ttl_seconds=300,
    )

    assert decision.ok is False
    assert decision.reason == expected_reason


@pytest.mark.asyncio
async def test_finalize_sync_success_and_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = FakePool()
    task_id = uuid7()
    user_id = uuid4()
    upsert_calls: list[str] = []

    async def fake_update_completed(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_get_reservation(*_args: object, **_kwargs: object) -> CreditReservation:
        return _reservation(task_id=task_id, user_id=user_id)

    async def fake_capture(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_upsert(*_args: object, **kwargs: object) -> None:
        upsert_calls.append(str(kwargs["status"]))

    monkeypatch.setattr(
        "solution2.services.billing.update_task_command_completed", fake_update_completed
    )
    monkeypatch.setattr("solution2.services.billing.get_credit_reservation", fake_get_reservation)
    monkeypatch.setattr("solution2.services.billing.capture_reservation", fake_capture)
    monkeypatch.setattr("solution2.services.billing.upsert_task_query_view", fake_upsert)

    ok = await billing._finalize_sync_success(
        db_pool=fake_pool,
        task_id=task_id,
        user_id=user_id,
        user_tier=SubscriptionTier.ENTERPRISE,
        model_class=ModelClass.SMALL,
        queue_name="queue.realtime",
        result_payload={"z": 7},
        runtime_ms=123,
    )
    assert ok is True
    assert upsert_calls == [TaskStatus.COMPLETED]

    async def fake_update_completed_false(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        "solution2.services.billing.update_task_command_completed",
        fake_update_completed_false,
    )
    not_ok = await billing._finalize_sync_success(
        db_pool=fake_pool,
        task_id=task_id,
        user_id=user_id,
        user_tier=SubscriptionTier.ENTERPRISE,
        model_class=ModelClass.SMALL,
        queue_name="queue.realtime",
        result_payload={"z": 7},
        runtime_ms=123,
    )
    assert not_ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (TaskStatus.TIMEOUT, "task_timeout_refund"),
        (TaskStatus.FAILED, "task_failed_refund"),
    ],
)
async def test_finalize_sync_failure_refund_paths(
    monkeypatch: pytest.MonkeyPatch,
    status: TaskStatus,
    expected_reason: str,
) -> None:
    fake_pool = FakePool()
    task_id = uuid7()
    user_id = uuid4()
    tx_reasons: list[str] = []
    upsert_statuses: list[str] = []

    async def fake_timed_out(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_failed(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_get_reservation(*_args: object, **_kwargs: object) -> CreditReservation:
        return _reservation(task_id=task_id, user_id=user_id)

    async def fake_release(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_add_credits(*_args: object, **_kwargs: object) -> int:
        return 110

    async def fake_insert_tx(*_args: object, **kwargs: object) -> None:
        tx_reasons.append(str(kwargs["reason"]))

    async def fake_upsert(*_args: object, **kwargs: object) -> None:
        upsert_statuses.append(str(kwargs["status"]))

    monkeypatch.setattr("solution2.services.billing.update_task_command_timed_out", fake_timed_out)
    monkeypatch.setattr("solution2.services.billing.update_task_command_failed", fake_failed)
    monkeypatch.setattr("solution2.services.billing.get_credit_reservation", fake_get_reservation)
    monkeypatch.setattr("solution2.services.billing.release_reservation", fake_release)
    monkeypatch.setattr("solution2.services.billing.add_user_credits", fake_add_credits)
    monkeypatch.setattr("solution2.services.billing.insert_credit_transaction", fake_insert_tx)
    monkeypatch.setattr("solution2.services.billing.upsert_task_query_view", fake_upsert)

    result = await billing._finalize_sync_failure(
        db_pool=fake_pool,
        task_id=task_id,
        user_id=user_id,
        user_tier=SubscriptionTier.ENTERPRISE,
        model_class=ModelClass.SMALL,
        queue_name="queue.realtime",
        status=status,
        error_message="simulated",
    )
    assert result is True
    assert tx_reasons == [expected_reason]
    assert upsert_statuses == [status]


@pytest.mark.asyncio
async def test_run_admission_gate_recovers_unique_violation_as_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()
    existing_task = uuid7()

    async def fake_execute(*_args: object, **_kwargs: object) -> AdmissionDecision:
        raise asyncpg.UniqueViolationError("duplicate")

    async def fake_get_by_idempotency(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(task_id=existing_task)

    monkeypatch.setattr("solution2.services.billing._execute_admission_transaction", fake_execute)
    monkeypatch.setattr(
        "solution2.services.billing.get_task_command_by_idempotency",
        fake_get_by_idempotency,
    )

    decision, sha = await run_admission_gate(
        admission_script_sha="sha-idem",
        user_id=uuid4(),
        task_id=uuid7(),
        cost=10,
        idempotency_value="idem-key",
        max_concurrent=3,
        db_pool=fake_pool,
        stream_payload={
            "x": 1,
            "y": 2,
            "tier": SubscriptionTier.PRO.value,
            "mode": RequestMode.ASYNC.value,
            "model_class": ModelClass.SMALL.value,
        },
    )
    assert decision.ok is False
    assert decision.reason == "IDEMPOTENT"
    assert decision.existing_task_id == str(existing_task)
    assert sha == "sha-idem"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side_effect", "expected_reason", "expected_sha"),
    [
        (asyncpg.LockNotAvailableError("locked"), "CONCURRENCY", "sha-batch"),
        (RuntimeError("boom"), "ERROR", ""),
    ],
)
async def test_run_batch_admission_gate_exception_paths(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Exception,
    expected_reason: str,
    expected_sha: str,
) -> None:
    fake_pool = FakePool()

    async def fake_execute(*_args: object, **_kwargs: object) -> BatchAdmissionResult:
        raise side_effect

    monkeypatch.setattr(
        "solution2.services.billing._execute_batch_admission_transaction", fake_execute
    )

    result, sha = await run_batch_admission_gate(
        admission_script_sha="sha-batch",
        user_id=uuid4(),
        user_tier=SubscriptionTier.PRO,
        batch_id=uuid7(),
        tasks=(BatchTaskSpec(x=1, y=2, model_class=ModelClass.SMALL),),
        max_concurrent=3,
        base_task_cost=10,
        db_pool=fake_pool,
    )
    assert result.ok is False
    assert result.reason == expected_reason
    assert sha == expected_sha


@pytest.mark.asyncio
async def test_run_sync_submission_maps_lock_contention_to_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = FakePool()

    async def fake_execute_sync_admission(**_: object) -> AdmissionDecision:
        raise asyncpg.LockNotAvailableError("lock busy")

    monkeypatch.setattr(
        "solution2.services.billing._execute_sync_admission_transaction",
        fake_execute_sync_admission,
    )
    decision, sha, sync_result = await run_sync_submission(
        admission_script_sha="sha-sync",
        user_id=uuid4(),
        user_tier=SubscriptionTier.ENTERPRISE,
        task_id=uuid7(),
        x=1,
        y=2,
        model_class=ModelClass.SMALL,
        cost=10,
        callback_url=None,
        idempotency_value="idem-lock",
        max_concurrent=3,
        queue_name="queue.realtime",
        execution_timeout_seconds=0.1,
        db_pool=fake_pool,
    )
    assert decision == AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None)
    assert sha == "sha-sync"
    assert sync_result is None
