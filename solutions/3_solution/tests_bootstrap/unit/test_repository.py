from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from solution3.constants import (
    BillingState,
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)
from solution3.db import repository


def _task_row(**overrides: object) -> dict[str, object]:
    now = datetime.now(tz=UTC)
    row: dict[str, object] = {
        "task_id": uuid4(),
        "user_id": UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        "tier": "pro",
        "mode": "async",
        "model_class": "small",
        "status": "PENDING",
        "billing_state": "RESERVED",
        "x": 2,
        "y": 3,
        "cost": 10,
        "tb_pending_transfer_id": uuid4(),
        "callback_url": None,
        "idempotency_key": "idem-1",
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _query_row(**overrides: object) -> dict[str, object]:
    now = datetime.now(tz=UTC)
    row: dict[str, object] = {
        "task_id": uuid4(),
        "user_id": UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        "tier": "pro",
        "mode": "async",
        "model_class": "small",
        "status": "COMPLETED",
        "billing_state": "CAPTURED",
        "result": {"sum": 5},
        "error": None,
        "runtime_ms": 2000,
        "projection_version": 1,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _outbox_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": uuid4(),
        "aggregate_id": uuid4(),
        "event_type": "task.requested",
        "topic": "tasks.requested",
        "payload": '{"task_id":"123"}',
        "created_at": datetime.now(tz=UTC),
    }
    row.update(overrides)
    return row


class FakeConnection:
    def __init__(self) -> None:
        self.fetchrow_results: list[dict[str, object] | None] = []
        self.fetch_results: list[list[dict[str, object]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetchrow_calls.append((query, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        if not self.fetch_results:
            return []
        return self.fetch_results.pop(0)

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "UPDATE 1"

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield


class FakePool:
    def __init__(self, *, connection: FakeConnection | None = None) -> None:
        self.connection = connection or FakeConnection()
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_results: list[dict[str, object] | None] = []
        self.fetch_results: list[list[dict[str, object]]] = []

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetchrow_calls.append((query, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        if not self.fetch_results:
            return []
        return self.fetch_results.pop(0)

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "UPDATE 1"

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeConnection]:
        yield self.connection


def test_row_mapping_helpers_return_domain_objects() -> None:
    command = repository._map_task_command(_task_row())
    query_view = repository._map_task_query_view(_query_row())
    outbox = repository._map_outbox_event(_outbox_row())

    assert command.tier == SubscriptionTier.PRO
    assert command.status == TaskStatus.PENDING
    assert query_view.billing_state == BillingState.CAPTURED
    assert query_view.result == {"sum": 5}
    assert outbox.topic == "tasks.requested"


@pytest.mark.asyncio
async def test_fetch_active_user_and_api_key_hash_validation() -> None:
    pool = FakePool()
    pool.fetchrow_results = [
        {
            "user_id": UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            "name": "user1",
            "role": "user",
            "tier": "pro",
        },
        {"exists": 1},
    ]

    user = await repository.fetch_active_user_by_api_key(pool, api_key="secret-key")
    is_active = await repository.is_active_api_key_hash(pool, "secret-key")

    assert user is not None
    assert user.api_key == "secret-key"
    assert user.role == UserRole.USER
    assert user.tier == SubscriptionTier.PRO
    assert is_active is True
    first_args = pool.fetchrow_calls[0][1]
    second_args = pool.fetchrow_calls[1][1]
    assert first_args == second_args
    assert len(cast(str, first_args[0])) == 64


@pytest.mark.asyncio
async def test_record_admin_credit_topup_persists_outbox_event() -> None:
    connection = FakeConnection()
    pool = FakePool(connection=connection)
    user_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    admin_user_id = UUID("5ba7f2f8-24be-448a-9552-3af6e06e8898")

    await repository.record_admin_credit_topup(
        pool,
        user_id=user_id,
        amount=25,
        reason="integration-topup",
        admin_user_id=admin_user_id,
        api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        new_balance=275,
    )

    assert len(connection.execute_calls) == 1
    query, args = connection.execute_calls[0]
    assert "INSERT INTO cmd.outbox_events" in query
    assert args[:3] == (user_id, "billing.topup", "billing.topup")
    payload = json.loads(cast(str, args[3]))
    assert payload == {
        "user_id": str(user_id),
        "amount": 25,
        "reason": "integration-topup",
        "admin_user_id": str(admin_user_id),
        "api_key": "c9169bc2-2980-4155-be29-442ffc44ce64",
        "new_balance": 275,
    }


@pytest.mark.asyncio
async def test_list_active_users_with_initial_credits_returns_pairs() -> None:
    pool = FakePool()
    pool.fetch_results = [
        [
            {"user_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), "initial_credits": 1000},
            {"user_id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), "initial_credits": 250},
        ]
    ]

    rows = await repository.list_active_users_with_initial_credits(pool)

    assert rows == [
        (UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), 1000),
        (UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), 250),
    ]


@pytest.mark.asyncio
async def test_get_task_command_and_query_view_map_rows() -> None:
    pool = FakePool()
    pool.fetchrow_results = [_task_row(), _query_row()]

    command = await repository.get_task_command(pool, uuid4())
    query_view = await repository.get_task_query_view(pool, uuid4())

    assert command is not None and command.model_class == ModelClass.SMALL
    assert query_view is not None and query_view.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_submit_task_command_returns_existing_idempotent_row() -> None:
    connection = FakeConnection()
    existing = _task_row()
    connection.fetchrow_results = [existing]
    pool = FakePool(connection=connection)

    created, command = await repository.submit_task_command(
        pool,
        task_id=uuid4(),
        user_id=cast(UUID, existing["user_id"]),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        x=1,
        y=2,
        cost=10,
        tb_pending_transfer_id=uuid4(),
        callback_url=None,
        idempotency_key="idem-1",
        outbox_payload={"task_id": "unused"},
    )

    assert created is False
    assert command.task_id == existing["task_id"]
    assert connection.execute_calls == []


@pytest.mark.asyncio
async def test_submit_task_command_inserts_command_and_outbox_event() -> None:
    connection = FakeConnection()
    inserted = _task_row(task_id=uuid4(), idempotency_key="idem-2")
    connection.fetchrow_results = [None, inserted]
    pool = FakePool(connection=connection)
    outbox_payload = {"task_id": str(inserted["task_id"]), "status": "PENDING"}

    created, command = await repository.submit_task_command(
        pool,
        task_id=cast(UUID, inserted["task_id"]),
        user_id=cast(UUID, inserted["user_id"]),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        x=2,
        y=3,
        cost=10,
        tb_pending_transfer_id=cast(UUID, inserted["tb_pending_transfer_id"]),
        callback_url="https://callback.example",
        idempotency_key="idem-2",
        outbox_payload=outbox_payload,
    )

    assert created is True
    assert command.task_id == inserted["task_id"]
    assert any("INSERT INTO cmd.outbox_events" in query for query, _ in connection.execute_calls)
    outbox_args = next(
        args for query, args in connection.execute_calls if "INSERT INTO cmd.outbox_events" in query
    )
    assert json.loads(cast(str, outbox_args[3])) == outbox_payload


@pytest.mark.asyncio
async def test_cancel_update_and_finalize_commands_emit_outbox_events() -> None:
    connection = FakeConnection()
    task_id = uuid4()
    user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")
    connection.fetchrow_results = [
        {"task_id": task_id, "user_id": user_id},
        {"task_id": task_id},
        {"task_id": task_id},
    ]
    pool = FakePool(connection=connection)

    cancelled = await repository.cancel_task_command(pool, task_id=task_id)
    running = await repository.update_task_running(pool, task_id=task_id)
    finalized = await repository.finalize_task_command(
        pool,
        task_id=task_id,
        user_id=user_id,
        status=TaskStatus.COMPLETED,
        billing_state=BillingState.CAPTURED,
        cost=10,
        result={"sum": 5},
        error=None,
    )

    assert cancelled is True
    assert running is True
    assert finalized is True
    execute_queries = [query for query, _ in connection.execute_calls]
    assert sum("INSERT INTO cmd.outbox_events" in query for query in execute_queries) == 3


@pytest.mark.asyncio
async def test_fetch_unpublished_outbox_events_and_mark_published() -> None:
    pool = FakePool()
    event_id = uuid4()
    pool.fetch_results = [[_outbox_row(event_id=event_id)]]

    events = await repository.fetch_unpublished_outbox_events(pool, limit=25)
    await repository.mark_outbox_events_published(pool, event_ids=[event_id])
    await repository.mark_outbox_events_published(pool, event_ids=[])

    assert [event.event_id for event in events] == [event_id]
    assert pool.fetch_calls[0][1] == (25,)
    assert pool.execute_calls == [
        (
            """
        UPDATE cmd.outbox_events
        SET published_at = now()
        WHERE event_id = ANY($1::uuid[])
        """,
            ([event_id],),
        )
    ]


@pytest.mark.asyncio
async def test_is_inbox_event_processed_checks_consumer_dedup_key() -> None:
    pool = FakePool()
    event_id = uuid4()
    pool.fetchrow_results = [{"exists": 1}, None]

    first = await repository.is_inbox_event_processed(
        pool,
        event_id=event_id,
        consumer_name="projector",
    )
    second = await repository.is_inbox_event_processed(
        pool,
        event_id=event_id,
        consumer_name="projector",
    )

    assert first is True
    assert second is False
    assert pool.fetchrow_calls[0][1] == (event_id, "projector")


@pytest.mark.asyncio
async def test_apply_task_projection_upserts_view_and_records_checkpoint() -> None:
    connection = FakeConnection()
    projected_row = _query_row(
        task_id=UUID("019c6db7-0857-7858-af93-f724ae4fe2c2"),
        status="COMPLETED",
        billing_state="CAPTURED",
        result={"sum": 5},
        error=None,
        projection_version=14,
    )
    connection.fetchrow_results = [projected_row]
    pool = FakePool(connection=connection)
    event_id = uuid4()

    view = await repository.apply_task_projection(
        pool,
        consumer_name="projector",
        projector_name="projector",
        topic="tasks.completed",
        partition_id=0,
        committed_offset=14,
        event_id=event_id,
        event={
            "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
            "result": {"sum": 5},
            "error": None,
        },
    )

    assert view is not None
    assert view.status == TaskStatus.COMPLETED
    assert view.billing_state == BillingState.CAPTURED
    assert view.projection_version == 14
    assert connection.execute_calls[0][1] == (event_id, "projector")
    assert connection.execute_calls[1][1] == ("projector", "tasks.completed", 0, 14)


@pytest.mark.asyncio
async def test_reset_projection_state_clears_only_projector_state() -> None:
    connection = FakeConnection()
    pool = FakePool(connection=connection)

    await repository.reset_projection_state(
        pool,
        consumer_names=("projector", "projector-rebuild"),
        projector_names=("projector", "projector-rebuild"),
    )

    assert connection.execute_calls[0] == ("TRUNCATE query.task_query_view", ())
    assert "DELETE FROM cmd.inbox_events" in connection.execute_calls[1][0]
    assert connection.execute_calls[1][1] == (["projector", "projector-rebuild"],)
    assert "DELETE FROM cmd.projection_checkpoints" in connection.execute_calls[2][0]
    assert connection.execute_calls[2][1] == (["projector", "projector-rebuild"],)


@pytest.mark.asyncio
async def test_rebuild_task_query_view_from_commands_returns_inserted_row_count() -> None:
    connection = FakeConnection()
    connection.fetch_results = [[{"task_id": uuid4()}, {"task_id": uuid4()}]]
    pool = FakePool(connection=connection)

    inserted = await repository.rebuild_task_query_view_from_commands(pool)

    assert inserted == 2
    assert len(connection.fetch_calls) == 1
    query, args = connection.fetch_calls[0]
    assert "INSERT INTO query.task_query_view" in query
    assert "FROM cmd.task_commands" in query
    assert args == ()


@pytest.mark.asyncio
async def test_list_stale_reserved_tasks_maps_rows() -> None:
    pool = FakePool()
    stale_row = _task_row(
        task_id=UUID("019c6db7-0857-7858-af93-f724ae4fe2c2"),
        status="PENDING",
        billing_state="RESERVED",
        tb_pending_transfer_id=UUID("019c6db7-1439-7ace-bd2b-e1a3bb03328c"),
    )
    pool.fetch_results = [[stale_row]]

    rows = await repository.list_stale_reserved_tasks(pool, stale_after_seconds=720)

    assert [row.task_id for row in rows] == [UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")]
    assert rows[0].billing_state == BillingState.RESERVED
    assert rows[0].status == TaskStatus.PENDING
    assert pool.fetch_calls[0][1][-1] == 720


@pytest.mark.asyncio
async def test_expire_stale_reserved_task_updates_state_and_emits_outbox() -> None:
    connection = FakeConnection()
    task_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    pending_transfer_id = UUID("019c6db7-1439-7ace-bd2b-e1a3bb03328c")
    connection.fetchrow_results = [
        {
            "task_id": task_id,
            "user_id": UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            "status": "EXPIRED",
            "billing_state": "EXPIRED",
            "model_class": "small",
        }
    ]
    pool = FakePool(connection=connection)

    reconciled = await repository.expire_stale_reserved_task(
        pool,
        task_id=task_id,
        tb_pending_transfer_id=pending_transfer_id,
        stale_after_seconds=720,
    )

    assert reconciled is not None
    assert reconciled.status == TaskStatus.EXPIRED
    assert reconciled.billing_state == BillingState.EXPIRED
    assert "UPDATE query.task_query_view" in connection.execute_calls[0][0]
    assert connection.execute_calls[1][1] == (task_id, pending_transfer_id)
    outbox_args = connection.execute_calls[2][1]
    assert outbox_args[:3] == (task_id, "task.expired", "tasks.expired")
    payload = json.loads(cast(str, outbox_args[3]))
    assert payload == {
        "task_id": str(task_id),
        "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
        "status": "EXPIRED",
        "billing_state": "EXPIRED",
    }


@pytest.mark.asyncio
async def test_align_stale_reserved_task_terminal_state_updates_completed_capture() -> None:
    connection = FakeConnection()
    task_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    pending_transfer_id = UUID("019c6db7-1439-7ace-bd2b-e1a3bb03328c")
    task = repository._map_stale_reserved_task(
        _task_row(
            task_id=task_id,
            status="RUNNING",
            billing_state="RESERVED",
            tb_pending_transfer_id=pending_transfer_id,
        )
    )
    connection.fetchrow_results = [
        {
            "task_id": task_id,
            "user_id": UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            "status": "COMPLETED",
            "billing_state": "CAPTURED",
            "model_class": "small",
        }
    ]
    pool = FakePool(connection=connection)

    reconciled = await repository.align_stale_reserved_task_terminal_state(
        pool,
        task=task,
        status=TaskStatus.COMPLETED,
        billing_state=BillingState.CAPTURED,
        resolution="TB_CAPTURED",
        stale_after_seconds=720,
    )

    assert reconciled is not None
    assert reconciled.status == TaskStatus.COMPLETED
    assert reconciled.billing_state == BillingState.CAPTURED
    assert "UPDATE query.task_query_view" in connection.execute_calls[0][0]
    assert connection.execute_calls[1][1] == (task_id, pending_transfer_id, "TB_CAPTURED")
    outbox_args = connection.execute_calls[2][1]
    assert outbox_args[:3] == (task_id, "task.completed", "tasks.completed")
    payload = json.loads(cast(str, outbox_args[3]))
    assert payload == {
        "task_id": str(task_id),
        "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
        "status": "COMPLETED",
        "billing_state": "CAPTURED",
        "result": None,
        "error": None,
    }


@pytest.mark.asyncio
async def test_align_stale_reserved_task_terminal_state_updates_cancelled_release() -> None:
    connection = FakeConnection()
    task_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    pending_transfer_id = UUID("019c6db7-1439-7ace-bd2b-e1a3bb03328c")
    task = repository._map_stale_reserved_task(
        _task_row(
            task_id=task_id,
            status="PENDING",
            billing_state="RESERVED",
            tb_pending_transfer_id=pending_transfer_id,
        )
    )
    connection.fetchrow_results = [
        {
            "task_id": task_id,
            "user_id": UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            "status": "CANCELLED",
            "billing_state": "RELEASED",
            "model_class": "small",
        }
    ]
    pool = FakePool(connection=connection)

    reconciled = await repository.align_stale_reserved_task_terminal_state(
        pool,
        task=task,
        status=TaskStatus.CANCELLED,
        billing_state=BillingState.RELEASED,
        resolution="TB_VOIDED",
        stale_after_seconds=720,
    )

    assert reconciled is not None
    assert reconciled.status == TaskStatus.CANCELLED
    assert reconciled.billing_state == BillingState.RELEASED
    assert connection.execute_calls[1][1] == (task_id, pending_transfer_id, "TB_VOIDED")
    outbox_args = connection.execute_calls[2][1]
    assert outbox_args[:3] == (task_id, "task.cancelled", "tasks.cancelled")
    payload = json.loads(cast(str, outbox_args[3]))
    assert payload == {
        "task_id": str(task_id),
        "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
        "status": "CANCELLED",
        "billing_state": "RELEASED",
        "result": None,
        "error": None,
    }


@pytest.mark.asyncio
async def test_get_task_callback_url_returns_optional_string() -> None:
    pool = FakePool()
    pool.fetchrow_results = [{"callback_url": "https://example.test/webhook"}, None]

    first = await repository.get_task_callback_url(
        pool, task_id=UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    )
    second = await repository.get_task_callback_url(
        pool, task_id=UUID("019c6db7-1439-7ace-bd2b-e1a3bb03328c")
    )

    assert first == "https://example.test/webhook"
    assert second is None


@pytest.mark.asyncio
async def test_insert_webhook_dead_letter_upserts_by_event_id() -> None:
    pool = FakePool()
    event_id = UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    task_id = UUID("019c6db7-1439-7ace-bd2b-e1a3bb03328c")
    payload = {"task_id": str(task_id), "status": "FAILED"}

    await repository.insert_webhook_dead_letter(
        pool,
        event_id=event_id,
        task_id=task_id,
        topic="tasks.failed",
        callback_url="https://example.test/webhook",
        payload=payload,
        attempts=3,
        last_error="status=503",
    )

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "INSERT INTO cmd.webhook_dead_letters" in query
    assert "ON CONFLICT (event_id) DO UPDATE" in query
    assert args[:4] == (event_id, task_id, "tasks.failed", "https://example.test/webhook")
    assert json.loads(cast(str, args[4])) == payload
    assert args[5:] == (3, "status=503")
