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
