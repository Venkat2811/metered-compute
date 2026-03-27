from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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
from solution3.models.domain import (
    AuthUser,
    OutboxEventRecord,
    ReconciledTaskState,
    StaleReservedTask,
    TaskCommand,
    TaskQueryView,
)

TERMINAL_PROJECTION_TOPICS = frozenset(
    {
        "tasks.completed",
        "tasks.failed",
        "tasks.cancelled",
        "tasks.expired",
    }
)


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
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
        except json.JSONDecodeError:
            decoded = None
        result = decoded if isinstance(decoded, dict) else None
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


def _map_stale_reserved_task(row: asyncpg.Record) -> StaleReservedTask:
    return StaleReservedTask(
        task_id=row["task_id"],
        user_id=row["user_id"],
        tier=SubscriptionTier(row["tier"]),
        mode=RequestMode(row["mode"]),
        model_class=ModelClass(row["model_class"]),
        status=TaskStatus(row["status"]),
        billing_state=BillingState(row["billing_state"]),
        tb_pending_transfer_id=row["tb_pending_transfer_id"],
        created_at=row["created_at"],
    )


def _terminal_event_for_status(status: TaskStatus) -> tuple[str, str]:
    if status == TaskStatus.COMPLETED:
        return "task.completed", "tasks.completed"
    if status == TaskStatus.FAILED:
        return "task.failed", "tasks.failed"
    if status == TaskStatus.CANCELLED:
        return "task.cancelled", "tasks.cancelled"
    if status == TaskStatus.EXPIRED:
        return "task.expired", "tasks.expired"
    raise ValueError(f"unsupported terminal task status: {status.value}")


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


async def list_active_users_with_initial_credits(pool: asyncpg.Pool) -> list[tuple[UUID, int]]:
    rows = await pool.fetch(
        """
        SELECT user_id, initial_credits
        FROM cmd.users
        WHERE is_active = true
        ORDER BY created_at, user_id
        """
    )
    return [(row["user_id"], int(row["initial_credits"])) for row in rows]


async def get_task_command(pool: asyncpg.Pool, task_id: UUID) -> TaskCommand | None:
    row = await pool.fetchrow("SELECT * FROM cmd.task_commands WHERE task_id=$1", task_id)
    return None if row is None else _map_task_command(row)


async def get_task_callback_url(pool: asyncpg.Pool, *, task_id: UUID) -> str | None:
    row = await pool.fetchrow(
        """
        SELECT callback_url
        FROM cmd.task_commands
        WHERE task_id=$1
        """,
        task_id,
    )
    if row is None:
        return None
    callback_url = row["callback_url"]
    return str(callback_url) if callback_url is not None else None


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


async def record_admin_credit_topup(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    amount: int,
    reason: str,
    admin_user_id: UUID,
    api_key_masked: str,
    new_balance: int,
    transfer_id: UUID,
) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            user_id,
            "billing.topup",
            "billing.topup",
            json.dumps(
                {
                    "user_id": str(user_id),
                    "amount": amount,
                    "reason": reason,
                    "admin_user_id": str(admin_user_id),
                    "target_api_key_masked": api_key_masked,
                    "new_balance": new_balance,
                    "transfer_id": str(transfer_id),
                }
            ),
        )


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


async def update_task_running(pool: asyncpg.Pool, *, task_id: UUID) -> bool:
    async with pool.acquire() as connection, connection.transaction():
        updated = await connection.fetchrow(
            """
            UPDATE cmd.task_commands
            SET status=$2, updated_at=now()
            WHERE task_id=$1 AND status=$3
            RETURNING task_id
            """,
            task_id,
            TaskStatus.RUNNING.value,
            TaskStatus.PENDING.value,
        )
        if updated is None:
            return False
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            task_id,
            "task.started",
            "tasks.started",
            json.dumps(
                {
                    "task_id": str(task_id),
                    "status": TaskStatus.RUNNING.value,
                }
            ),
        )
        return True


async def finalize_task_command(
    pool: asyncpg.Pool,
    *,
    task_id: UUID,
    user_id: UUID,
    status: TaskStatus,
    billing_state: BillingState,
    cost: int,
    result: dict[str, Any] | None,
    error: str | None,
) -> bool:
    topic = "tasks.completed" if status == TaskStatus.COMPLETED else "tasks.failed"
    event_type = "task.completed" if status == TaskStatus.COMPLETED else "task.failed"
    async with pool.acquire() as connection, connection.transaction():
        updated = await connection.fetchrow(
            """
            UPDATE cmd.task_commands
            SET status=$2, billing_state=$3, updated_at=now()
            WHERE task_id=$1 AND status IN ($4, $5)
            RETURNING task_id
            """,
            task_id,
            status.value,
            billing_state.value,
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
        )
        if updated is None:
            return False
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            task_id,
            event_type,
            topic,
            json.dumps(
                {
                    "task_id": str(task_id),
                    "user_id": str(user_id),
                    "status": status.value,
                    "billing_state": billing_state.value,
                    "cost": cost,
                    "result": result,
                    "error": error,
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


async def insert_webhook_dead_letter(
    pool: asyncpg.Pool,
    *,
    event_id: UUID,
    task_id: UUID,
    topic: str,
    callback_url: str,
    payload: Mapping[str, Any],
    attempts: int,
    last_error: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO cmd.webhook_dead_letters(
          event_id,
          task_id,
          topic,
          callback_url,
          payload,
          attempts,
          last_error
        )
        VALUES($1, $2, $3, $4, $5::jsonb, $6, $7)
        ON CONFLICT (event_id) DO UPDATE SET
          attempts = GREATEST(cmd.webhook_dead_letters.attempts, EXCLUDED.attempts),
          last_error = EXCLUDED.last_error,
          updated_at = now()
        """,
        event_id,
        task_id,
        topic,
        callback_url,
        json.dumps(dict(payload)),
        attempts,
        last_error,
    )


async def is_inbox_event_processed(
    pool: asyncpg.Pool,
    *,
    event_id: UUID,
    consumer_name: str,
) -> bool:
    row = await pool.fetchrow(
        """
        SELECT 1
        FROM cmd.inbox_events
        WHERE event_id=$1 AND consumer_name=$2
        """,
        event_id,
        consumer_name,
    )
    return row is not None


async def apply_task_projection(
    pool: asyncpg.Pool,
    *,
    consumer_name: str,
    projector_name: str,
    topic: str,
    partition_id: int,
    committed_offset: int,
    event_id: UUID,
    event: Mapping[str, Any],
) -> TaskQueryView | None:
    task_id = UUID(str(event["task_id"]))
    overwrite_terminal_fields = topic in TERMINAL_PROJECTION_TOPICS
    result_payload = event.get("result") if overwrite_terminal_fields else None
    error_value = str(event["error"]) if event.get("error") is not None else None

    async with pool.acquire() as connection, connection.transaction():
        projected_row = await connection.fetchrow(
            """
            INSERT INTO query.task_query_view(
              task_id,
              user_id,
              tier,
              mode,
              model_class,
              status,
              billing_state,
              result,
              error,
              runtime_ms,
              projection_version,
              created_at,
              updated_at
            )
            SELECT
              command.task_id,
              command.user_id,
              command.tier,
              command.mode,
              command.model_class,
              command.status,
              command.billing_state,
              CASE WHEN $2 THEN $3::jsonb ELSE NULL END,
              CASE WHEN $2 THEN $4 ELSE NULL END,
              NULL,
              $5,
              command.created_at,
              command.updated_at
            FROM cmd.task_commands AS command
            WHERE command.task_id = $1
            ON CONFLICT (task_id) DO UPDATE SET
              user_id = EXCLUDED.user_id,
              tier = EXCLUDED.tier,
              mode = EXCLUDED.mode,
              model_class = EXCLUDED.model_class,
              status = EXCLUDED.status,
              billing_state = EXCLUDED.billing_state,
              result = CASE
                WHEN $2 THEN EXCLUDED.result
                ELSE query.task_query_view.result
              END,
              error = CASE
                WHEN $2 THEN EXCLUDED.error
                ELSE query.task_query_view.error
              END,
              runtime_ms = EXCLUDED.runtime_ms,
              projection_version = GREATEST(
                query.task_query_view.projection_version,
                EXCLUDED.projection_version
              ),
              updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            task_id,
            overwrite_terminal_fields,
            json.dumps(result_payload) if result_payload is not None else None,
            error_value,
            committed_offset,
        )
        if projected_row is None:
            return None

        await connection.execute(
            """
            INSERT INTO cmd.inbox_events(event_id, consumer_name)
            VALUES($1, $2)
            """,
            event_id,
            consumer_name,
        )
        await connection.execute(
            """
            INSERT INTO cmd.projection_checkpoints(
              projector_name,
              topic,
              partition_id,
              committed_offset
            )
            VALUES($1, $2, $3, $4)
            ON CONFLICT (projector_name) DO UPDATE SET
              topic = EXCLUDED.topic,
              partition_id = EXCLUDED.partition_id,
              committed_offset = EXCLUDED.committed_offset,
              updated_at = now()
            """,
            projector_name,
            topic,
            partition_id,
            committed_offset,
        )
        return _map_task_query_view(projected_row)


async def reset_projection_state(
    pool: asyncpg.Pool,
    *,
    consumer_names: tuple[str, ...],
    projector_names: tuple[str, ...],
) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("TRUNCATE query.task_query_view")
        if consumer_names:
            await connection.execute(
                """
                DELETE FROM cmd.inbox_events
                WHERE consumer_name = ANY($1::text[])
                """,
                list(consumer_names),
            )
        if projector_names:
            await connection.execute(
                """
                DELETE FROM cmd.projection_checkpoints
                WHERE projector_name = ANY($1::text[])
                """,
                list(projector_names),
            )


async def rebuild_task_query_view_from_commands(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as connection, connection.transaction():
        rows = await connection.fetch(
            """
            INSERT INTO query.task_query_view(
              task_id,
              user_id,
              tier,
              mode,
              model_class,
              status,
              billing_state,
              result,
              error,
              runtime_ms,
              projection_version,
              created_at,
              updated_at
            )
            SELECT
              task_id,
              user_id,
              tier,
              mode,
              model_class,
              status,
              billing_state,
              NULL,
              NULL,
              NULL,
              0,
              created_at,
              updated_at
            FROM cmd.task_commands
            ORDER BY created_at, task_id
            RETURNING task_id
            """
        )
    return len(rows)


async def list_stale_reserved_tasks(
    pool: asyncpg.Pool, *, stale_after_seconds: int
) -> list[StaleReservedTask]:
    rows = await pool.fetch(
        """
        SELECT
          task_id,
          user_id,
          tier,
          mode,
          model_class,
          status,
          billing_state,
          tb_pending_transfer_id,
          created_at
        FROM cmd.task_commands
        WHERE billing_state=$1
          AND status IN ($2, $3)
          AND created_at < now() - make_interval(secs => $4)
        ORDER BY created_at, task_id
        """,
        BillingState.RESERVED.value,
        TaskStatus.PENDING.value,
        TaskStatus.RUNNING.value,
        stale_after_seconds,
    )
    return [_map_stale_reserved_task(row) for row in rows]


async def expire_stale_reserved_task(
    pool: asyncpg.Pool,
    *,
    task_id: UUID,
    tb_pending_transfer_id: UUID,
    stale_after_seconds: int,
) -> ReconciledTaskState | None:
    async with pool.acquire() as connection, connection.transaction():
        updated = await connection.fetchrow(
            """
            UPDATE cmd.task_commands
            SET status=$2, billing_state=$3, updated_at=now()
            WHERE task_id=$1
              AND billing_state=$4
              AND status IN ($5, $6)
              AND created_at < now() - make_interval(secs => $7)
            RETURNING task_id, user_id, status, billing_state, model_class
            """,
            task_id,
            TaskStatus.EXPIRED.value,
            BillingState.EXPIRED.value,
            BillingState.RESERVED.value,
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            stale_after_seconds,
        )
        if updated is None:
            return None

        await connection.execute(
            """
            UPDATE query.task_query_view
            SET status=$2, billing_state=$3, updated_at=now()
            WHERE task_id=$1
            """,
            task_id,
            TaskStatus.EXPIRED.value,
            BillingState.EXPIRED.value,
        )
        await connection.execute(
            """
            INSERT INTO cmd.billing_reconcile_jobs(
              task_id,
              tb_pending_transfer_id,
              state,
              resolution
            )
            VALUES($1, $2, 'RESOLVED', 'TB_AUTO_EXPIRED')
            """,
            task_id,
            tb_pending_transfer_id,
        )
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            task_id,
            "task.expired",
            "tasks.expired",
            json.dumps(
                {
                    "task_id": str(updated["task_id"]),
                    "user_id": str(updated["user_id"]),
                    "status": TaskStatus.EXPIRED.value,
                    "billing_state": BillingState.EXPIRED.value,
                }
            ),
        )
        return ReconciledTaskState(
            task_id=updated["task_id"],
            user_id=updated["user_id"],
            status=TaskStatus(updated["status"]),
            billing_state=BillingState(updated["billing_state"]),
            model_class=ModelClass(updated["model_class"]),
        )


async def align_stale_reserved_task_terminal_state(
    pool: asyncpg.Pool,
    *,
    task: StaleReservedTask,
    status: TaskStatus,
    billing_state: BillingState,
    resolution: str,
    stale_after_seconds: int,
) -> ReconciledTaskState | None:
    event_type, topic = _terminal_event_for_status(status)

    async with pool.acquire() as connection, connection.transaction():
        updated = await connection.fetchrow(
            """
            UPDATE cmd.task_commands
            SET status=$2, billing_state=$3, updated_at=now()
            WHERE task_id=$1
              AND billing_state=$4
              AND status IN ($5, $6)
              AND created_at < now() - make_interval(secs => $7)
            RETURNING task_id, user_id, status, billing_state, model_class
            """,
            task.task_id,
            status.value,
            billing_state.value,
            BillingState.RESERVED.value,
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
            stale_after_seconds,
        )
        if updated is None:
            return None

        await connection.execute(
            """
            UPDATE query.task_query_view
            SET status=$2,
                billing_state=$3,
                result=NULL,
                error=NULL,
                updated_at=now()
            WHERE task_id=$1
            """,
            task.task_id,
            status.value,
            billing_state.value,
        )
        await connection.execute(
            """
            INSERT INTO cmd.billing_reconcile_jobs(
              task_id,
              tb_pending_transfer_id,
              state,
              resolution
            )
            VALUES($1, $2, 'RESOLVED', $3)
            """,
            task.task_id,
            task.tb_pending_transfer_id,
            resolution,
        )
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4::jsonb)
            """,
            task.task_id,
            event_type,
            topic,
            json.dumps(
                {
                    "task_id": str(updated["task_id"]),
                    "user_id": str(updated["user_id"]),
                    "status": status.value,
                    "billing_state": billing_state.value,
                    "result": None,
                    "error": None,
                }
            ),
        )
        return ReconciledTaskState(
            task_id=updated["task_id"],
            user_id=updated["user_id"],
            status=TaskStatus(updated["status"]),
            billing_state=BillingState(updated["billing_state"]),
            model_class=ModelClass(updated["model_class"]),
        )
