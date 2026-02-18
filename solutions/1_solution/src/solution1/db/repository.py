from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import asyncpg

from solution1.constants import SubscriptionTier, TaskStatus, UserRole
from solution1.models.domain import AuthUser, TaskRecord, WebhookSubscription

type DBExecutor = asyncpg.Connection | asyncpg.Pool


DB_POOL_ACQUIRE_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def _acquire_db_connection(
    pool: asyncpg.Pool,
    *,
    timeout_seconds: float = DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
) -> AsyncIterator[asyncpg.Connection]:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with pool.acquire() as connection:
                yield connection
    except TimeoutError as exc:
        raise TimeoutError(
            f"Timed out waiting {timeout_seconds:.1f}s for PostgreSQL connection from pool"
        ) from exc


def _rows_affected(command_tag: str) -> int:
    return int(command_tag.split(" ")[-1])


def _parse_task_result(raw_result: object) -> dict[str, object] | None:
    if raw_result is None:
        return None
    if isinstance(raw_result, dict):
        return {str(key): value for key, value in raw_result.items()}
    if isinstance(raw_result, str):
        decoded = json.loads(raw_result)
        if isinstance(decoded, dict):
            return {str(key): value for key, value in decoded.items()}
        return None
    return None


def _task_from_record(record: asyncpg.Record) -> TaskRecord:
    raw_result = record["result"]
    parsed_result = _parse_task_result(raw_result)

    return TaskRecord(
        task_id=UUID(str(record["task_id"])),
        api_key=str(record["api_key"]),
        user_id=UUID(str(record["user_id"])),
        x=int(record["x"]),
        y=int(record["y"]),
        cost=int(record["cost"]),
        status=cast(TaskStatus, str(record["status"])),
        result=parsed_result,
        error=str(record["error"]) if record["error"] is not None else None,
        runtime_ms=int(record["runtime_ms"]) if record["runtime_ms"] is not None else None,
        idempotency_key=(
            str(record["idempotency_key"]) if record["idempotency_key"] is not None else None
        ),
        created_at=record["created_at"],
        started_at=record["started_at"],
        completed_at=record["completed_at"],
    )


def _webhook_subscription_from_record(record: asyncpg.Record) -> WebhookSubscription:
    return WebhookSubscription(
        user_id=UUID(str(record["user_id"])),
        callback_url=str(record["callback_url"]),
        enabled=bool(record["enabled"]),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


async def fetch_user_by_api_key(pool: asyncpg.Pool, api_key: str) -> AuthUser | None:
    row = await pool.fetchrow(
        """
        SELECT api_key, user_id, name, role, credits, tier
        FROM users
        WHERE api_key=$1
        """,
        api_key,
    )
    if row is None:
        return None

    return AuthUser(
        api_key=str(row["api_key"]),
        user_id=UUID(str(row["user_id"])),
        name=str(row["name"]),
        role=UserRole(str(row["role"])),
        credits=int(row["credits"]),
        tier=SubscriptionTier(str(row["tier"])),
    )


async def fetch_user_credits_by_api_key(pool: asyncpg.Pool, api_key: str) -> int | None:
    value = await pool.fetchval("SELECT credits FROM users WHERE api_key=$1", api_key)
    if value is None:
        return None
    return int(value)


async def is_active_api_key_hash(pool: asyncpg.Pool, api_key: str) -> bool:
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    exists = await pool.fetchval(
        """
        SELECT EXISTS(
          SELECT 1 FROM api_keys
          WHERE key_hash=$1 AND is_active=true
        )
        """,
        key_hash,
    )
    return bool(exists)


async def insert_revoked_jti(
    executor: DBExecutor,
    *,
    jti: str,
    user_id: UUID,
    expires_at: datetime,
) -> None:
    await executor.execute(
        """
        INSERT INTO token_revocations(jti, user_id, expires_at)
        VALUES($1, $2, $3)
        """,
        jti,
        user_id,
        expires_at,
    )


async def is_jti_revoked(pool: asyncpg.Pool, *, jti: str) -> bool:
    exists = await pool.fetchval(
        """
        SELECT EXISTS(
          SELECT 1
          FROM token_revocations
          WHERE jti=$1 AND expires_at > now()
          LIMIT 1
        )
        """,
        jti,
    )
    return bool(exists)


async def load_active_revoked_jtis(
    pool: asyncpg.Pool,
    *,
    since: datetime,
) -> list[tuple[str, UUID, str]]:
    rows = await pool.fetch(
        """
        SELECT
          jti,
          user_id,
          to_char((revoked_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS day_iso
        FROM token_revocations
        WHERE revoked_at >= $1
          AND expires_at > now()
        ORDER BY revoked_at DESC
        """,
        since,
    )
    return [(str(row["jti"]), UUID(str(row["user_id"])), str(row["day_iso"])) for row in rows]


async def create_task_record(
    executor: DBExecutor,
    *,
    task_id: UUID,
    api_key: str,
    user_id: UUID,
    x: int,
    y: int,
    cost: int,
    idempotency_key: str | None,
) -> None:
    await executor.execute(
        """
        INSERT INTO tasks(task_id, api_key, user_id, x, y, cost, status, idempotency_key)
        VALUES($1, $2, $3, $4, $5, $6, 'PENDING', $7)
        """,
        task_id,
        api_key,
        user_id,
        x,
        y,
        cost,
        idempotency_key,
    )


async def insert_credit_transaction(
    executor: DBExecutor,
    *,
    user_id: UUID,
    task_id: UUID | None,
    delta: int,
    reason: str,
) -> None:
    await executor.execute(
        """
        INSERT INTO credit_transactions(user_id, task_id, delta, reason)
        VALUES($1, $2, $3, $4)
        """,
        user_id,
        task_id,
        delta,
        reason,
    )


async def get_task(executor: DBExecutor, task_id: UUID) -> TaskRecord | None:
    row = await executor.fetchrow(
        """
        SELECT task_id, api_key, user_id, x, y, cost, status, result,
               error, runtime_ms, idempotency_key, created_at, started_at, completed_at
        FROM tasks
        WHERE task_id=$1
        """,
        task_id,
    )
    if row is None:
        return None
    return _task_from_record(row)


async def update_task_running(executor: DBExecutor, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE tasks
        SET status='RUNNING', started_at=COALESCE(started_at, now())
        WHERE task_id=$1 AND status='PENDING'
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def update_task_completed(
    executor: DBExecutor,
    *,
    task_id: UUID,
    result_payload: dict[str, int],
    runtime_ms: int,
) -> bool:
    command = await executor.execute(
        """
        UPDATE tasks
        SET status='COMPLETED',
            result=$2::jsonb,
            runtime_ms=$3,
            completed_at=now()
        WHERE task_id=$1 AND status='RUNNING'
        """,
        task_id,
        json.dumps(result_payload),
        runtime_ms,
    )
    return _rows_affected(command) == 1


async def update_task_failed(executor: DBExecutor, *, task_id: UUID, error: str) -> bool:
    command = await executor.execute(
        """
        UPDATE tasks
        SET status='FAILED', error=$2, completed_at=now()
        WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
        """,
        task_id,
        error,
    )
    return _rows_affected(command) == 1


async def update_task_cancelled(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE tasks
        SET status='CANCELLED', completed_at=now()
        WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def update_task_expired(executor: DBExecutor, *, task_id: UUID) -> None:
    await executor.execute(
        """
        UPDATE tasks
        SET status='EXPIRED'
        WHERE task_id=$1 AND status IN ('COMPLETED', 'FAILED', 'CANCELLED')
        """,
        task_id,
    )


async def bulk_expire_old_terminal_tasks(pool: asyncpg.Pool, *, older_than_seconds: int) -> int:
    command = await pool.execute(
        """
        UPDATE tasks
        SET status='EXPIRED'
        WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')
          AND completed_at IS NOT NULL
          AND completed_at < now() - make_interval(secs => $1)
        """,
        older_than_seconds,
    )
    return int(command.split(" ")[-1])


async def purge_old_credit_transactions(
    pool: asyncpg.Pool, *, older_than_seconds: int, batch_size: int
) -> int:
    if older_than_seconds <= 0 or batch_size <= 0:
        return 0

    cutoff = datetime.now(tz=UTC) - timedelta(seconds=older_than_seconds)
    command = await pool.execute(
        """
        DELETE FROM credit_transactions
        WHERE txn_id IN (
          SELECT txn_id
          FROM credit_transactions
          WHERE created_at <= $1
          ORDER BY created_at ASC
          LIMIT $2
        )
        """,
        cutoff,
        batch_size,
    )
    return _rows_affected(command)


async def purge_old_credit_drift_audit(
    pool: asyncpg.Pool, *, older_than_seconds: int, batch_size: int
) -> int:
    if older_than_seconds <= 0 or batch_size <= 0:
        return 0

    cutoff = datetime.now(tz=UTC) - timedelta(seconds=older_than_seconds)
    command = await pool.execute(
        """
        DELETE FROM credit_drift_audit
        WHERE audit_id IN (
          SELECT audit_id
          FROM credit_drift_audit
          WHERE checked_at <= $1
          ORDER BY checked_at ASC
          LIMIT $2
        )
        """,
        cutoff,
        batch_size,
    )
    return _rows_affected(command)


async def task_exists(pool: asyncpg.Pool, task_id: UUID) -> bool:
    exists = await pool.fetchval("SELECT EXISTS(SELECT 1 FROM tasks WHERE task_id=$1)", task_id)
    return bool(exists)


async def admin_update_user_credits(
    pool: asyncpg.Pool,
    *,
    target_api_key: str,
    delta: int,
    reason: str,
) -> tuple[UUID, int] | None:
    row = await pool.fetchrow(
        """
        WITH updated AS (
            UPDATE users
            SET credits=credits+$1, updated_at=now()
            WHERE api_key=$2
            RETURNING user_id, credits
        ),
        credit_audit AS (
            INSERT INTO credit_transactions(user_id, task_id, delta, reason)
            SELECT user_id, NULL, $1, $3
            FROM updated
            RETURNING 1
        )
        SELECT user_id, credits
        FROM updated
        """,
        delta,
        target_api_key,
        reason,
    )
    if row is None:
        return None
    return UUID(str(row["user_id"])), int(row["credits"])


async def admin_update_user_credits_transactional(
    pool: asyncpg.Pool,
    *,
    target_api_key: str,
    delta: int,
    reason: str,
) -> tuple[UUID, int] | None:
    """Two-statement reference path used by scripts/benchmark_write_patterns.py."""

    async with _acquire_db_connection(pool) as connection, connection.transaction():
        row = await connection.fetchrow(
            """
            UPDATE users
            SET credits=credits+$1, updated_at=now()
            WHERE api_key=$2
            RETURNING user_id, credits
            """,
            delta,
            target_api_key,
        )
        if row is None:
            return None

        user_id = UUID(str(row["user_id"]))
        credits = int(row["credits"])
        await connection.execute(
            """
            INSERT INTO credit_transactions(user_id, task_id, delta, reason)
            VALUES($1, NULL, $2, $3)
            """,
            user_id,
            delta,
            reason,
        )

        return user_id, credits


async def upsert_webhook_subscription(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    callback_url: str,
    enabled: bool = True,
) -> WebhookSubscription:
    row = await pool.fetchrow(
        """
        INSERT INTO webhook_subscriptions(user_id, callback_url, enabled, updated_at)
        VALUES($1, $2, $3, now())
        ON CONFLICT (user_id)
        DO UPDATE SET
            callback_url=EXCLUDED.callback_url,
            enabled=EXCLUDED.enabled,
            updated_at=now()
        RETURNING user_id, callback_url, enabled, created_at, updated_at
        """,
        user_id,
        callback_url,
        enabled,
    )
    if row is None:
        raise RuntimeError("failed to upsert webhook subscription")
    return _webhook_subscription_from_record(row)


async def get_webhook_subscription(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
) -> WebhookSubscription | None:
    row = await pool.fetchrow(
        """
        SELECT user_id, callback_url, enabled, created_at, updated_at
        FROM webhook_subscriptions
        WHERE user_id=$1
        """,
        user_id,
    )
    if row is None:
        return None
    return _webhook_subscription_from_record(row)


async def disable_webhook_subscription(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
) -> WebhookSubscription | None:
    row = await pool.fetchrow(
        """
        UPDATE webhook_subscriptions
        SET enabled=false, updated_at=now()
        WHERE user_id=$1
        RETURNING user_id, callback_url, enabled, created_at, updated_at
        """,
        user_id,
    )
    if row is None:
        return None
    return _webhook_subscription_from_record(row)


async def insert_webhook_dead_letter(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    task_id: UUID,
    event_payload: dict[str, object],
    last_error: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO webhook_delivery_dead_letters(user_id, task_id, event_payload, last_error)
        VALUES($1, $2, $3::jsonb, $4)
        """,
        user_id,
        task_id,
        json.dumps(event_payload),
        last_error,
    )


async def list_stuck_running_tasks(pool: asyncpg.Pool, *, timeout_seconds: int) -> list[TaskRecord]:
    rows = await pool.fetch(
        """
        SELECT task_id, api_key, user_id, x, y, cost, status, result,
               error, runtime_ms, idempotency_key, created_at, started_at, completed_at
        FROM tasks
        WHERE status='RUNNING'
          AND started_at IS NOT NULL
          AND started_at < now() - make_interval(secs => $1)
        """,
        timeout_seconds,
    )
    return [_task_from_record(row) for row in rows]


async def upsert_credit_snapshot(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    balance: int,
    snapshot_at: datetime,
) -> None:
    await pool.execute(
        """
        INSERT INTO credit_snapshots(user_id, balance, snapshot_at)
        VALUES($1, $2, $3)
        ON CONFLICT (user_id)
        DO UPDATE SET balance=EXCLUDED.balance, snapshot_at=EXCLUDED.snapshot_at
        """,
        user_id,
        balance,
        snapshot_at,
    )


async def list_credit_snapshots(pool: asyncpg.Pool) -> list[tuple[UUID, int]]:
    rows = await pool.fetch(
        """
        SELECT user_id, balance
        FROM credit_snapshots
        """
    )
    return [(UUID(str(row["user_id"])), int(row["balance"])) for row in rows]


async def insert_credit_drift_audit(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    redis_balance: int,
    db_balance: int,
    drift: int,
    action_taken: str | None,
) -> None:
    await pool.execute(
        """
        INSERT INTO credit_drift_audit(user_id, redis_balance, db_balance, drift, action_taken)
        VALUES($1, $2, $3, $4, $5)
        """,
        user_id,
        redis_balance,
        db_balance,
        drift,
        action_taken,
    )


async def upsert_stream_checkpoint(
    pool: asyncpg.Pool,
    *,
    consumer_group: str,
    last_stream_id: str,
) -> None:
    await pool.execute(
        """
        INSERT INTO stream_checkpoints(consumer_group, last_stream_id, updated_at)
        VALUES($1, $2, now())
        ON CONFLICT (consumer_group)
        DO UPDATE SET last_stream_id=EXCLUDED.last_stream_id, updated_at=EXCLUDED.updated_at
        """,
        consumer_group,
        last_stream_id,
    )
