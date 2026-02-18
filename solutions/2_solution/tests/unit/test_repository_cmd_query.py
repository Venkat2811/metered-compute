from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from solution2.constants import (
    RequestMode,
    ReservationState,
    SubscriptionTier,
    TaskStatus,
)
from solution2.db.repository import (
    add_user_credits,
    bulk_expire_query_results,
    capture_reservation,
    check_inbox_event,
    count_active_reservations,
    count_total_active_reservations,
    create_outbox_event,
    create_reservation,
    create_task_command,
    find_expired_reservations,
    get_credit_reservation,
    get_task_command,
    get_task_query_view,
    list_unpublished_outbox_events,
    lock_user_for_admission,
    mark_outbox_event_published,
    purge_old_outbox_events,
    record_inbox_event,
    release_reservation,
    update_task_command_cancelled,
    update_task_command_completed,
    update_task_command_failed,
    update_task_command_running,
    update_task_command_status,
    update_task_command_timed_out,
    upsert_task_query_view,
)
from solution2.models.domain import TaskCommand, TaskQueryView
from tests.constants import ALT_USER_ID, TASK_ID_PRIMARY, TEST_USER_ID


class _FakeExecutor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrows: list[tuple[str, tuple[object, ...]]] = []
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

        self.fetchrow_result: dict[str, object] | None = None
        self.fetch_result: list[dict[str, object]] = []
        self.execute_result = "UPDATE 0 1"

    async def execute(self, query: str, *args: object) -> str:
        self.executions.append((query, args))
        return self.execute_result

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetchrows.append((query, args))
        return self.fetchrow_result

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetches.append((query, args))
        return self.fetch_result


class _FakePool(_FakeExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchval_result = 1

    async def fetchval(self, query: str, *args: object) -> object:
        self.fetchval_calls.append((query, args))
        return self.fetchval_result


@pytest.mark.asyncio
async def test_create_task_command_inserts_cmd_row() -> None:
    executor = _FakeExecutor()
    task_id = TASK_ID_PRIMARY

    await create_task_command(
        executor,
        task_id=task_id,
        user_id=TEST_USER_ID,
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class="small",
        x=1,
        y=2,
        cost=5,
        callback_url=None,
        idempotency_key="idem-1",
    )

    query, args = executor.executions[0]
    assert "INSERT INTO cmd.task_commands" in query
    assert args == (
        task_id,
        TEST_USER_ID,
        SubscriptionTier.PRO.value,
        RequestMode.ASYNC.value,
        "small",
        1,
        2,
        5,
        None,
        "idem-1",
    )


@pytest.mark.asyncio
async def test_get_task_command_parses_record() -> None:
    executor = _FakeExecutor()
    command = TASK_ID_PRIMARY
    executor.fetchrow_result = {
        "task_id": str(TASK_ID_PRIMARY),
        "user_id": str(TEST_USER_ID),
        "tier": "free",
        "mode": "async",
        "model_class": "medium",
        "status": "PENDING",
        "x": 11,
        "y": 22,
        "cost": 33,
        "callback_url": "https://cb",
        "idempotency_key": "idem",
        "created_at": datetime(2026, 2, 17, tzinfo=UTC),
        "updated_at": datetime(2026, 2, 17, tzinfo=UTC),
    }

    parsed = await get_task_command(executor, command)

    assert isinstance(parsed, TaskCommand)
    assert parsed.task_id == TASK_ID_PRIMARY
    assert parsed.tier.value == "free"
    assert parsed.model_class.value == "medium"
    assert isinstance(parsed.status, TaskStatus)
    assert parsed.status == TaskStatus.PENDING
    assert parsed.callback_url == "https://cb"


@pytest.mark.asyncio
async def test_update_task_command_status_returns_true_on_single_row_update() -> None:
    executor = _FakeExecutor()

    changed = await update_task_command_status(
        executor,
        task_id=TASK_ID_PRIMARY,
        status=TaskStatus.RUNNING,
    )

    assert changed is True
    query, args = executor.executions[0]
    assert "UPDATE cmd.task_commands" in query
    assert args == (TASK_ID_PRIMARY, TaskStatus.RUNNING.value)


@pytest.mark.asyncio
async def test_update_task_command_transition_helpers_are_guarded() -> None:
    executor = _FakeExecutor()

    running = await update_task_command_running(executor, task_id=TASK_ID_PRIMARY)
    completed = await update_task_command_completed(executor, task_id=TASK_ID_PRIMARY)
    failed = await update_task_command_failed(executor, task_id=TASK_ID_PRIMARY)
    timed_out = await update_task_command_timed_out(executor, task_id=TASK_ID_PRIMARY)
    cancelled = await update_task_command_cancelled(executor, task_id=TASK_ID_PRIMARY)

    assert running is True
    assert completed is True
    assert failed is True
    assert timed_out is True
    assert cancelled is True
    assert "status='PENDING'" in executor.executions[0][0]
    assert "status IN ('PENDING', 'RUNNING')" in executor.executions[1][0]
    assert "status IN ('PENDING', 'RUNNING')" in executor.executions[2][0]
    assert "status IN ('PENDING', 'RUNNING')" in executor.executions[3][0]
    assert "status IN ('PENDING', 'RUNNING')" in executor.executions[4][0]


@pytest.mark.asyncio
async def test_create_reservation_inserts_reserved_state() -> None:
    executor = _FakeExecutor()
    expires_at = datetime(2026, 2, 17, tzinfo=UTC)

    await create_reservation(
        executor,
        task_id=TASK_ID_PRIMARY,
        user_id=TEST_USER_ID,
        amount=7,
        expires_at=expires_at,
    )

    query, args = executor.executions[0]
    assert "INSERT INTO cmd.credit_reservations" in query
    assert args == (TASK_ID_PRIMARY, TEST_USER_ID, 7, expires_at)


@pytest.mark.asyncio
async def test_get_credit_reservation_supports_for_update() -> None:
    executor = _FakeExecutor()
    now = datetime(2026, 2, 17, tzinfo=UTC)
    executor.fetchrow_result = {
        "reservation_id": "11111111-1111-1111-1111-111111111111",
        "task_id": str(TASK_ID_PRIMARY),
        "user_id": str(TEST_USER_ID),
        "amount": 7,
        "state": ReservationState.RESERVED.value,
        "expires_at": now + timedelta(minutes=5),
        "created_at": now,
        "updated_at": now,
    }

    reservation = await get_credit_reservation(
        executor,
        task_id=TASK_ID_PRIMARY,
        for_update=True,
    )

    assert reservation is not None
    assert reservation.task_id == TASK_ID_PRIMARY
    assert reservation.state == ReservationState.RESERVED
    query, args = executor.fetchrows[0]
    assert "FROM cmd.credit_reservations" in query
    assert "FOR UPDATE" in query
    assert args == (TASK_ID_PRIMARY,)


@pytest.mark.asyncio
async def test_add_user_credits_updates_balance() -> None:
    executor = _FakeExecutor()
    executor.fetchrow_result = {"credits": 123}

    balance = await add_user_credits(executor, user_id=TEST_USER_ID, delta=10)

    assert balance == 123
    query, args = executor.fetchrows[0]
    assert "UPDATE users" in query
    assert "RETURNING credits" in query
    assert args == (TEST_USER_ID, 10)


@pytest.mark.asyncio
async def test_capture_and_release_reservation_return_bools() -> None:
    executor = _FakeExecutor()
    released = await capture_reservation(executor, task_id=TASK_ID_PRIMARY)
    reset = await release_reservation(executor, task_id=TASK_ID_PRIMARY)

    assert released is True
    assert reset is True
    assert len(executor.executions) == 2


@pytest.mark.asyncio
async def test_lock_user_for_admission_uses_row_lock() -> None:
    pool = _FakePool()
    pool.fetchval_result = 1

    locked = await lock_user_for_admission(pool, user_id=TEST_USER_ID)

    assert locked is True
    query, args = pool.fetchval_calls[0]
    assert "FOR UPDATE" in query
    assert args == (TEST_USER_ID,)


@pytest.mark.asyncio
async def test_count_active_reservations_uses_expected_filter() -> None:
    pool = _FakePool()
    pool.fetchval_result = 3

    count = await count_active_reservations(pool, user_id=TEST_USER_ID)

    assert count == 3
    query, args = pool.fetchval_calls[0]
    assert "SELECT COALESCE(COUNT(*), 0)" in query
    assert args == (TEST_USER_ID,)


@pytest.mark.asyncio
async def test_count_total_active_reservations_uses_reserved_filter_only() -> None:
    pool = _FakePool()
    pool.fetchval_result = 9

    count = await count_total_active_reservations(pool)

    assert count == 9
    query, args = pool.fetchval_calls[0]
    assert "FROM cmd.credit_reservations" in query
    assert "state='RESERVED'" in query
    assert args == ()


@pytest.mark.asyncio
async def test_find_expired_reservations_filters_reserved_rows() -> None:
    pool = _FakePool()
    as_of = datetime(2026, 2, 17, tzinfo=UTC)
    pool.fetch_result = [
        {
            "reservation_id": "12345678-1111-1111-1111-111111111111",
            "task_id": str(TASK_ID_PRIMARY),
            "user_id": str(TEST_USER_ID),
            "amount": 7,
            "state": ReservationState.RESERVED.value,
            "expires_at": as_of - timedelta(seconds=10),
            "created_at": as_of,
            "updated_at": as_of,
        }
    ]

    expired = await find_expired_reservations(pool, as_of=as_of)

    assert len(expired) == 1
    assert expired[0].state == ReservationState.RESERVED
    query, args = pool.fetches[0]
    assert "state='RESERVED'" in query
    assert args == (as_of,)


@pytest.mark.asyncio
async def test_create_outbox_event_and_mark_published_roundtrip() -> None:
    executor = _FakeExecutor()
    executor.fetchrow_result = {"event_id": "11111111-1111-1111-1111-111111111111"}

    event_id = await create_outbox_event(
        executor,
        aggregate_id=TASK_ID_PRIMARY,
        event_type="task.submitted",
        routing_key="tasks.realtime.free.small",
        payload={"a": 1},
    )

    assert event_id == UUID("11111111-1111-1111-1111-111111111111")
    assert len(executor.fetchrows) == 1
    assert "INSERT INTO cmd.outbox_events" in executor.fetchrows[0][0]
    list_pool = _FakePool()
    list_pool.fetch_result = [
        {
            "event_id": str(event_id),
            "aggregate_id": str(TEST_USER_ID),
            "event_type": "task.submitted",
            "routing_key": "tasks.realtime.free.small",
            "payload": {"a": 1},
            "published_at": None,
            "created_at": datetime(2026, 2, 17, tzinfo=UTC),
        }
    ]
    events = await list_unpublished_outbox_events(list_pool, limit=20)
    assert len(events) == 1
    assert events[0].routing_key == "tasks.realtime.free.small"

    marked = await mark_outbox_event_published(list_pool, event_id=event_id)
    assert marked is True
    pool_query, pool_args = list_pool.executions[0]
    assert "UPDATE cmd.outbox_events" in pool_query
    assert pool_args == (event_id,)


@pytest.mark.asyncio
async def test_purge_old_outbox_events_removes_old_published_rows() -> None:
    pool = _FakePool()
    pool.execute_result = "DELETE 3"

    deleted = await purge_old_outbox_events(
        pool,
        older_than_seconds=604800,
        batch_size=250,
    )

    assert deleted == 3
    query, params = pool.executions[0]
    assert "DELETE FROM cmd.outbox_events" in query
    assert "published_at <= $1" in query
    assert "LIMIT $2" in query
    assert len(params) == 2
    assert params[1] == 250


@pytest.mark.parametrize(
    "older_than_seconds,batch_size",
    [
        (0, 500),
        (600, 0),
    ],
)
@pytest.mark.asyncio
async def test_purge_old_outbox_events_noop_for_invalid_window(
    older_than_seconds: int,
    batch_size: int,
) -> None:
    pool = _FakePool()
    deleted = await purge_old_outbox_events(
        pool,
        older_than_seconds=older_than_seconds,
        batch_size=batch_size,
    )

    assert deleted == 0
    assert not pool.executions


@pytest.mark.asyncio
async def test_query_view_upsert_and_fetch() -> None:
    executor = _FakeExecutor()
    await upsert_task_query_view(
        executor,
        task_id=TASK_ID_PRIMARY,
        user_id=ALT_USER_ID,
        tier=SubscriptionTier.ENTERPRISE,
        mode=RequestMode.BATCH,
        model_class="large",
        status=TaskStatus.RUNNING,
        result={"z": 9},
        error=None,
        queue_name="queue.batch",
        runtime_ms=123,
    )
    query, args = executor.executions[0]
    assert "INSERT INTO query.task_query_view" in query
    assert "created_at, updated_at" in query
    assert args == (
        TASK_ID_PRIMARY,
        ALT_USER_ID,
        SubscriptionTier.ENTERPRISE.value,
        RequestMode.BATCH.value,
        "large",
        TaskStatus.RUNNING.value,
        '{"z": 9}',
        None,
        "queue.batch",
        123,
    )

    executor.fetchrow_result = {
        "task_id": str(TASK_ID_PRIMARY),
        "user_id": str(ALT_USER_ID),
        "tier": SubscriptionTier.ENTERPRISE.value,
        "mode": RequestMode.BATCH.value,
        "model_class": "large",
        "status": TaskStatus.RUNNING.value,
        "result": {"z": 9},
        "error": None,
        "queue_name": "queue.batch",
        "runtime_ms": 123,
        "created_at": datetime(2026, 2, 17, tzinfo=UTC),
        "updated_at": datetime(2026, 2, 17, tzinfo=UTC),
    }
    view = await get_task_query_view(executor, TASK_ID_PRIMARY)
    assert isinstance(view, TaskQueryView)
    assert isinstance(view.status, TaskStatus)
    assert view.status == TaskStatus.RUNNING
    assert view.runtime_ms == 123


@pytest.mark.asyncio
async def test_bulk_expire_query_results_deletes_rows() -> None:
    pool = _FakePool()
    pool.execute_result = "DELETE 7"
    removed = await bulk_expire_query_results(pool, older_than_seconds=1800)
    assert removed == 7
    query, args = pool.executions[0]
    assert "DELETE FROM query.task_query_view" in query
    assert args == (1800,)


@pytest.mark.asyncio
async def test_inbox_idempotency_checks() -> None:
    pool = _FakePool()
    pool.fetchval_result = False
    existing = await check_inbox_event(pool, event_id=TASK_ID_PRIMARY, consumer_name="outbox-relay")
    assert existing is False
    inserted = await record_inbox_event(
        pool, event_id=TASK_ID_PRIMARY, consumer_name="outbox-relay"
    )
    assert inserted is True
