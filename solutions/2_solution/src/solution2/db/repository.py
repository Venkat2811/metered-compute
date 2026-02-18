from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from solution2.constants import (
    ModelClass,
    RequestMode,
    ReservationState,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)
from solution2.models.domain import (
    AuthUser,
    CreditReservation,
    OutboxEvent,
    TaskCommand,
    TaskQueryView,
    WebhookSubscription,
)

type DBExecutor = asyncpg.Connection | asyncpg.Pool


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


def _parse_task_payload(raw_payload: object) -> dict[str, object]:
    return _parse_task_result(raw_payload) or {}


def _task_command_from_record(record: asyncpg.Record) -> TaskCommand:
    return TaskCommand(
        task_id=UUID(str(record["task_id"])),
        user_id=UUID(str(record["user_id"])),
        tier=SubscriptionTier(str(record["tier"])),
        mode=RequestMode(str(record["mode"])),
        model_class=ModelClass(str(record["model_class"])),
        status=TaskStatus(str(record["status"])),
        x=int(record["x"]),
        y=int(record["y"]),
        cost=int(record["cost"]),
        callback_url=(str(record["callback_url"]) if record["callback_url"] is not None else None),
        idempotency_key=(
            str(record["idempotency_key"]) if record["idempotency_key"] is not None else None
        ),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


def _credit_reservation_from_record(record: asyncpg.Record) -> CreditReservation:
    return CreditReservation(
        reservation_id=UUID(str(record["reservation_id"])),
        task_id=UUID(str(record["task_id"])),
        user_id=UUID(str(record["user_id"])),
        amount=int(record["amount"]),
        state=ReservationState(str(record["state"])),
        expires_at=record["expires_at"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


def _outbox_event_from_record(record: asyncpg.Record) -> OutboxEvent:
    return OutboxEvent(
        event_id=UUID(str(record["event_id"])),
        aggregate_id=UUID(str(record["aggregate_id"])),
        event_type=str(record["event_type"]),
        routing_key=str(record["routing_key"]),
        payload=_parse_task_payload(record["payload"]),
        published_at=record["published_at"],
        created_at=record["created_at"],
    )


def _task_query_view_from_record(record: asyncpg.Record) -> TaskQueryView:
    return TaskQueryView(
        task_id=UUID(str(record["task_id"])),
        user_id=UUID(str(record["user_id"])),
        tier=SubscriptionTier(str(record["tier"])),
        mode=RequestMode(str(record["mode"])),
        model_class=ModelClass(str(record["model_class"])),
        status=TaskStatus(str(record["status"])),
        result=_parse_task_payload(record["result"]),
        error=(str(record["error"]) if record["error"] is not None else None),
        queue_name=(str(record["queue_name"]) if record["queue_name"] is not None else None),
        runtime_ms=(int(record["runtime_ms"]) if record["runtime_ms"] is not None else None),
        created_at=record["created_at"],
        updated_at=record["updated_at"],
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


async def create_task_command(
    executor: DBExecutor,
    *,
    task_id: UUID,
    user_id: UUID,
    tier: SubscriptionTier,
    mode: RequestMode,
    model_class: str,
    x: int,
    y: int,
    cost: int,
    callback_url: str | None,
    idempotency_key: str | None,
) -> None:
    await executor.execute(
        """
        INSERT INTO cmd.task_commands(
            task_id, user_id, tier, mode, model_class, x, y, cost, callback_url, idempotency_key
        )
        VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        task_id,
        user_id,
        tier.value,
        mode.value,
        model_class,
        x,
        y,
        cost,
        callback_url,
        idempotency_key,
    )


async def get_task_command(executor: DBExecutor, task_id: UUID) -> TaskCommand | None:
    row = await executor.fetchrow(
        """
        SELECT
            task_id, user_id, tier, mode, model_class, status, x, y, cost,
            callback_url, idempotency_key, created_at, updated_at
        FROM cmd.task_commands
        WHERE task_id=$1
        """,
        task_id,
    )
    if row is None:
        return None
    return _task_command_from_record(row)


async def get_task_command_by_idempotency(
    executor: DBExecutor,
    *,
    user_id: UUID,
    idempotency_key: str,
) -> TaskCommand | None:
    row = await executor.fetchrow(
        """
        SELECT
            task_id, user_id, tier, mode, model_class, status, x, y, cost,
            callback_url, idempotency_key, created_at, updated_at
        FROM cmd.task_commands
        WHERE user_id=$1 AND idempotency_key=$2
        """,
        user_id,
        idempotency_key,
    )
    if row is None:
        return None
    return _task_command_from_record(row)


async def update_task_command_status(
    executor: DBExecutor,
    *,
    task_id: UUID,
    status: TaskStatus,
) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.task_commands
        SET status=$2, updated_at=now()
        WHERE task_id=$1
        """,
        task_id,
        status.value,
    )
    return _rows_affected(command) == 1


async def update_task_command_running(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.task_commands
        SET status='RUNNING', updated_at=now()
        WHERE task_id=$1 AND status='PENDING'
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def update_task_command_completed(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.task_commands
        SET status='COMPLETED', updated_at=now()
        WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def update_task_command_failed(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.task_commands
        SET status='FAILED', updated_at=now()
        WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def update_task_command_timed_out(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.task_commands
        SET status='TIMEOUT', updated_at=now()
        WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def update_task_command_cancelled(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.task_commands
        SET status='CANCELLED', updated_at=now()
        WHERE task_id=$1 AND status IN ('PENDING', 'RUNNING')
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def get_user_credits_for_update(
    executor: DBExecutor,
    *,
    user_id: UUID,
) -> int | None:
    row = await executor.fetchrow(
        """
        SELECT credits
        FROM users
        WHERE user_id=$1
        FOR UPDATE
        """,
        user_id,
    )
    if row is None:
        return None
    return int(row["credits"])


async def reserve_user_credits(
    executor: DBExecutor,
    *,
    user_id: UUID,
    amount: int,
) -> int | None:
    row = await executor.fetchrow(
        """
        UPDATE users
        SET credits=credits-$2, updated_at=now()
        WHERE user_id=$1 AND credits >= $2
        RETURNING credits
        """,
        user_id,
        amount,
    )
    if row is None:
        return None
    return int(row["credits"])


async def create_reservation(
    executor: DBExecutor,
    *,
    task_id: UUID,
    user_id: UUID,
    amount: int,
    expires_at: datetime,
) -> None:
    await executor.execute(
        """
        INSERT INTO cmd.credit_reservations(task_id, user_id, amount, state, expires_at)
        VALUES($1, $2, $3, 'RESERVED', $4)
        """,
        task_id,
        user_id,
        amount,
        expires_at,
    )


async def capture_reservation(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.credit_reservations
        SET state='CAPTURED', updated_at=now()
        WHERE task_id=$1 AND state='RESERVED'
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def release_reservation(executor: DBExecutor, *, task_id: UUID) -> bool:
    command = await executor.execute(
        """
        UPDATE cmd.credit_reservations
        SET state='RELEASED', updated_at=now()
        WHERE task_id=$1 AND state='RESERVED'
        """,
        task_id,
    )
    return _rows_affected(command) == 1


async def get_credit_reservation(
    executor: DBExecutor,
    *,
    task_id: UUID,
    for_update: bool = False,
) -> CreditReservation | None:
    query = """
        SELECT reservation_id, task_id, user_id, amount, state, expires_at, created_at, updated_at
        FROM cmd.credit_reservations
        WHERE task_id=$1
    """
    if for_update:
        query += " FOR UPDATE"
    row = await executor.fetchrow(query, task_id)
    if row is None:
        return None
    return _credit_reservation_from_record(row)


async def add_user_credits(
    executor: DBExecutor,
    *,
    user_id: UUID,
    delta: int,
) -> int | None:
    row = await executor.fetchrow(
        """
        UPDATE users
        SET credits=credits+$2, updated_at=now()
        WHERE user_id=$1
        RETURNING credits
        """,
        user_id,
        delta,
    )
    if row is None:
        return None
    return int(row["credits"])


async def lock_user_for_admission(executor: DBExecutor, *, user_id: UUID) -> bool:
    """Lock a user row so concurrent admissions serialize per user."""

    locked = await executor.fetchval(
        """
        SELECT 1
        FROM users
        WHERE user_id=$1
        FOR UPDATE
        """,
        user_id,
    )
    return bool(locked)


async def count_active_reservations(pool: asyncpg.Pool, *, user_id: UUID) -> int:
    count = await pool.fetchval(
        """
        SELECT COALESCE(COUNT(*), 0)
        FROM cmd.credit_reservations
        WHERE user_id=$1 AND state='RESERVED'
          AND expires_at > now()
        """,
        user_id,
    )
    return int(count) if count is not None else 0


async def count_total_active_reservations(pool: asyncpg.Pool) -> int:
    count = await pool.fetchval(
        """
        SELECT COALESCE(COUNT(*), 0)
        FROM cmd.credit_reservations
        WHERE state='RESERVED'
          AND expires_at > now()
        """
    )
    return int(count) if count is not None else 0


async def find_expired_reservations(
    pool: asyncpg.Pool,
    *,
    as_of: datetime,
) -> list[CreditReservation]:
    rows = await pool.fetch(
        """
        SELECT reservation_id, task_id, user_id, amount, state, expires_at, created_at, updated_at
        FROM cmd.credit_reservations
        WHERE state='RESERVED' AND expires_at <= $1
        ORDER BY expires_at ASC
        """,
        as_of,
    )
    return [_credit_reservation_from_record(row) for row in rows]


async def create_outbox_event(
    executor: DBExecutor,
    *,
    aggregate_id: UUID,
    event_type: str,
    routing_key: str,
    payload: dict[str, object],
) -> UUID:
    row = await executor.fetchrow(
        """
        INSERT INTO cmd.outbox_events(aggregate_id, event_type, routing_key, payload)
        VALUES($1, $2, $3, $4::jsonb)
        RETURNING event_id
        """,
        aggregate_id,
        event_type,
        routing_key,
        json.dumps(payload),
    )
    if row is None:
        raise RuntimeError("failed to create outbox event")
    return UUID(str(row["event_id"]))


async def list_unpublished_outbox_events(
    pool: asyncpg.Pool, *, limit: int = 100
) -> list[OutboxEvent]:
    rows = await pool.fetch(
        """
        SELECT event_id, aggregate_id, event_type, routing_key, payload, published_at, created_at
        FROM cmd.outbox_events
        WHERE published_at IS NULL
        ORDER BY created_at ASC
        LIMIT $1
        """,
        limit,
    )
    return [_outbox_event_from_record(row) for row in rows]


async def mark_outbox_event_published(pool: asyncpg.Pool, *, event_id: UUID) -> bool:
    command = await pool.execute(
        """
        UPDATE cmd.outbox_events
        SET published_at=now()
        WHERE event_id=$1 AND published_at IS NULL
        """,
        event_id,
    )
    return _rows_affected(command) == 1


async def purge_old_outbox_events(
    pool: asyncpg.Pool, *, older_than_seconds: int, batch_size: int
) -> int:
    """Delete a bounded batch of old published outbox rows."""

    if older_than_seconds <= 0 or batch_size <= 0:
        return 0

    cutoff = datetime.now(tz=UTC) - timedelta(seconds=older_than_seconds)
    command = await pool.execute(
        """
        DELETE FROM cmd.outbox_events
        WHERE event_id IN (
          SELECT event_id
          FROM cmd.outbox_events
          WHERE published_at IS NOT NULL
            AND published_at <= $1
          ORDER BY published_at ASC
          LIMIT $2
        )
        """,
        cutoff,
        batch_size,
    )
    return _rows_affected(command)


async def check_inbox_event(executor: DBExecutor, *, event_id: UUID, consumer_name: str) -> bool:
    exists = await executor.fetchval(
        """
        SELECT EXISTS(
          SELECT 1 FROM cmd.inbox_events
          WHERE event_id=$1 AND consumer_name=$2
        )
        """,
        event_id,
        consumer_name,
    )
    return bool(exists)


async def record_inbox_event(
    executor: DBExecutor,
    *,
    event_id: UUID,
    consumer_name: str,
) -> bool:
    command = await executor.execute(
        """
        INSERT INTO cmd.inbox_events(event_id, consumer_name)
        VALUES($1, $2)
        ON CONFLICT DO NOTHING
        """,
        event_id,
        consumer_name,
    )
    return _rows_affected(command) == 1


async def upsert_task_query_view(
    executor: DBExecutor,
    *,
    task_id: UUID,
    user_id: UUID,
    tier: SubscriptionTier,
    mode: RequestMode,
    model_class: str,
    status: TaskStatus,
    result: dict[str, object] | None,
    error: str | None,
    queue_name: str | None,
    runtime_ms: int | None,
) -> None:
    await executor.execute(
        """
        INSERT INTO query.task_query_view(
          task_id, user_id, tier, mode, model_class, status, result, error, queue_name, runtime_ms,
          created_at, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, now(), now())
        ON CONFLICT(task_id) DO UPDATE SET
          user_id=EXCLUDED.user_id,
          tier=EXCLUDED.tier,
          mode=EXCLUDED.mode,
          model_class=EXCLUDED.model_class,
          status=EXCLUDED.status,
          result=EXCLUDED.result,
          error=EXCLUDED.error,
          queue_name=EXCLUDED.queue_name,
          runtime_ms=EXCLUDED.runtime_ms,
          updated_at=now()
        """,
        task_id,
        user_id,
        tier.value,
        mode.value,
        model_class,
        status.value,
        json.dumps(result) if result is not None else None,
        error,
        queue_name,
        runtime_ms,
    )


async def get_task_query_view(executor: DBExecutor, task_id: UUID) -> TaskQueryView | None:
    row = await executor.fetchrow(
        """
        SELECT
            task_id, user_id, tier, mode, model_class, status, result, error,
            queue_name, runtime_ms, created_at, updated_at
        FROM query.task_query_view
        WHERE task_id=$1
        """,
        task_id,
    )
    if row is None:
        return None
    return _task_query_view_from_record(row)


async def bulk_expire_query_results(pool: asyncpg.Pool, *, older_than_seconds: int) -> int:
    command = await pool.execute(
        """
        DELETE FROM query.task_query_view
        WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'EXPIRED')
          AND updated_at < now() - make_interval(secs => $1)
        """,
        older_than_seconds,
    )
    return _rows_affected(command)


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


async def admin_update_user_credits(
    executor: DBExecutor,
    *,
    target_api_key: str,
    delta: int,
    reason: str,
) -> tuple[UUID, int, int] | None:
    row = await executor.fetchrow(
        """
        WITH target AS (
            SELECT user_id, credits AS old_credits
            FROM users
            WHERE api_key=$2
            FOR UPDATE
        ),
        updated AS (
            UPDATE users u
            SET credits=credits+$1, updated_at=now()
            FROM target
            WHERE u.user_id=target.user_id
            RETURNING u.user_id, target.old_credits, u.credits AS new_credits
        ),
        credit_audit AS (
            INSERT INTO credit_transactions(user_id, task_id, delta, reason)
            SELECT user_id, NULL, $1, $3
            FROM updated
            RETURNING 1
        )
        SELECT user_id, old_credits, new_credits
        FROM updated
        """,
        delta,
        target_api_key,
        reason,
    )
    if row is None:
        return None
    return (
        UUID(str(row["user_id"])),
        int(row["old_credits"]),
        int(row["new_credits"]),
    )


async def admin_update_user_credits_transactional(
    pool: asyncpg.Pool,
    *,
    target_api_key: str,
    delta: int,
    reason: str,
) -> tuple[UUID, int] | None:
    """Two-statement reference path used by scripts/benchmark_write_patterns.py."""

    async with pool.acquire() as connection, connection.transaction():
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
