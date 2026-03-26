from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import asyncpg

from solution3.constants import (
    BillingState,
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)
from solution3.models.domain import AuthUser, OutboxEventRecord, TaskCommand, TaskQueryView


def _map_task_command(row: asyncpg.Record) -> TaskCommand:
    return TaskCommand(
        task_id=row["task_id"],
        user_id=row["user_id"],
        tier=SubscriptionTier(row["tier"]),
        mode=RequestMode(row["mode"]),
        model_class=ModelClass(row["model_class"]),
        status=TaskStatus(row["status"]),
        billing_state=BillingState(row["billing_state"]),
        x=row["x"],
        y=row["y"],
        cost=row["cost"],
        tb_pending_transfer_id=row["tb_pending_transfer_id"],
        callback_url=row["callback_url"],
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_task_query_view(row: asyncpg.Record) -> TaskQueryView:
    result = row["result"]
    return TaskQueryView(
        task_id=row["task_id"],
        user_id=row["user_id"],
        tier=SubscriptionTier(row["tier"]),
        mode=RequestMode(row["mode"]),
        model_class=ModelClass(row["model_class"]),
        status=TaskStatus(row["status"]),
        billing_state=BillingState(row["billing_state"]),
        result=result if isinstance(result, dict) else None,
        error=row["error"],
        runtime_ms=row["runtime_ms"],
        projection_version=row["projection_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _map_outbox_event(row: asyncpg.Record) -> OutboxEventRecord:
    return OutboxEventRecord(
        event_id=row["event_id"],
        aggregate_id=row["aggregate_id"],
        event_type=row["event_type"],
        topic=row["topic"],
        payload=row["payload"],
        created_at=row["created_at"],
    )


async def fetch_active_user_by_api_key(pool: asyncpg.Pool, *, api_key: str) -> AuthUser | None:
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    row = await pool.fetchrow(
        """
        SELECT u.user_id, u.name, u.role, u.tier
        FROM cmd.api_keys AS k
        JOIN cmd.users AS u ON u.user_id = k.user_id
        WHERE k.key_hash = $1 AND k.is_active = true AND u.is_active = true
        """,
        key_hash,
    )
    if row is None:
        return None
    return AuthUser(
        api_key=api_key,
        user_id=row["user_id"],
        name=row["name"],
        role=UserRole(row["role"]),
        tier=SubscriptionTier(row["tier"]),
        scopes=frozenset(),
    )


async def is_active_api_key_hash(pool: asyncpg.Pool, api_key: str) -> bool:
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    row = await pool.fetchrow(
        "SELECT 1 FROM cmd.api_keys WHERE key_hash=$1 AND is_active=true",
        key_hash,
    )
    return row is not None


async def get_task_command(pool: asyncpg.Pool, task_id: UUID) -> TaskCommand | None:
    row = await pool.fetchrow("SELECT * FROM cmd.task_commands WHERE task_id=$1", task_id)
    return None if row is None else _map_task_command(row)


async def get_task_query_view(pool: asyncpg.Pool, task_id: UUID) -> TaskQueryView | None:
    row = await pool.fetchrow("SELECT * FROM query.task_query_view WHERE task_id=$1", task_id)
    return None if row is None else _map_task_query_view(row)


async def submit_task_command(
    pool: asyncpg.Pool,
    *,
    task_id: UUID,
    user_id: UUID,
    tier: SubscriptionTier,
    mode: RequestMode,
    model_class: ModelClass,
    x: int,
    y: int,
    cost: int,
    tb_pending_transfer_id: UUID,
    callback_url: str | None,
    idempotency_key: str | None,
    outbox_payload: dict[str, Any],
) -> tuple[bool, TaskCommand]:
    async with pool.acquire() as connection, connection.transaction():
        existing_row = None
        if idempotency_key is not None:
            existing_row = await connection.fetchrow(
                """
                SELECT *
                FROM cmd.task_commands
                WHERE user_id=$1 AND idempotency_key=$2
                """,
                user_id,
                idempotency_key,
            )
        if existing_row is not None:
            return False, _map_task_command(existing_row)

        inserted_row = await connection.fetchrow(
            """
            INSERT INTO cmd.task_commands(
              task_id,
              user_id,
              tier,
              mode,
              model_class,
              status,
              billing_state,
              x,
              y,
              cost,
              tb_pending_transfer_id,
              callback_url,
              idempotency_key
            )
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING *
            """,
            task_id,
            user_id,
            tier.value,
            mode.value,
            model_class.value,
            TaskStatus.PENDING.value,
            BillingState.RESERVED.value,
            x,
            y,
            cost,
            tb_pending_transfer_id,
            callback_url,
            idempotency_key,
        )
        if inserted_row is None:
            raise RuntimeError("task command insert did not return a row")
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            task_id,
            "task.requested",
            "tasks.requested",
            json.dumps(outbox_payload),
        )
        return True, _map_task_command(inserted_row)


async def cancel_task_command(pool: asyncpg.Pool, *, task_id: UUID) -> bool:
    async with pool.acquire() as connection, connection.transaction():
        updated = await connection.fetchrow(
            """
            UPDATE cmd.task_commands
            SET status=$2, billing_state=$3, updated_at=now()
            WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
            RETURNING task_id, user_id
            """,
            task_id,
            TaskStatus.CANCELLED.value,
            BillingState.RELEASED.value,
        )
        if updated is None:
            return False
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            task_id,
            "task.cancelled",
            "tasks.cancelled",
            json.dumps(
                {
                    "task_id": str(task_id),
                    "user_id": str(updated["user_id"]),
                    "status": TaskStatus.CANCELLED.value,
                    "billing_state": BillingState.RELEASED.value,
                }
            ),
        )
        return True


async def fetch_unpublished_outbox_events(
    pool: asyncpg.Pool, *, limit: int = 100
) -> list[OutboxEventRecord]:
    rows = await pool.fetch(
        """
        SELECT event_id, aggregate_id, event_type, payload::text AS payload, topic, created_at
        FROM cmd.outbox_events
        WHERE published_at IS NULL
        ORDER BY created_at
        LIMIT $1
        """,
        limit,
    )
    return [_map_outbox_event(row) for row in rows]


async def mark_outbox_events_published(pool: asyncpg.Pool, *, event_ids: list[UUID]) -> None:
    if not event_ids:
        return
    await pool.execute(
        """
        UPDATE cmd.outbox_events
        SET published_at = now()
        WHERE event_id = ANY($1::uuid[])
        """,
        event_ids,
    )
