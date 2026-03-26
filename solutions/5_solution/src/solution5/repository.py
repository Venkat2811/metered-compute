"""Postgres repository — tasks and users. Billing state is NOT here (it's in TigerBeetle)."""

from __future__ import annotations

from typing import Any

import asyncpg
import structlog

log = structlog.get_logger()


async def run_migrations(pool: asyncpg.Pool, migrations_dir: str = "migrations") -> None:
    """Execute SQL migration files in order."""
    import pathlib

    migration_path = pathlib.Path(migrations_dir)
    async with pool.acquire() as conn:
        for sql_file in sorted(migration_path.glob("*.sql")):
            sql = sql_file.read_text()
            try:
                await conn.execute(sql)
                log.info("migration_applied", file=sql_file.name)
            except asyncpg.exceptions.DuplicateTableError:
                log.debug("migration_skipped_exists", file=sql_file.name)
            except asyncpg.exceptions.UniqueViolationError:
                log.debug("migration_skipped_seed_exists", file=sql_file.name)


async def get_user_by_api_key(pool: asyncpg.Pool, api_key: str) -> dict[str, Any] | None:
    """Look up user by plaintext API key (like Sol 0)."""
    row = await pool.fetchrow(
        """
        SELECT u.user_id, u.name, u.credits, u.role
        FROM api_keys k JOIN users u ON k.user_id = u.user_id
        WHERE k.api_key = $1 AND k.is_active = true
        """,
        api_key,
    )
    return dict(row) if row else None


async def create_task(
    pool: asyncpg.Pool,
    *,
    task_id: str,
    user_id: str,
    x: int,
    y: int,
    cost: int,
    tb_transfer_id: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Insert a new task row. Returns existing task if idempotency_key matches."""
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO tasks (task_id, user_id, x, y, cost, tb_transfer_id, idempotency_key)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            task_id,
            user_id,
            x,
            y,
            cost,
            tb_transfer_id,
            idempotency_key,
        )
        return dict(row)
    except asyncpg.exceptions.UniqueViolationError:
        if idempotency_key is not None:
            existing = await pool.fetchrow(
                """
                SELECT * FROM tasks
                WHERE user_id = $1::uuid AND idempotency_key = $2
                """,
                user_id,
                idempotency_key,
            )
            if existing:
                return dict(existing)
        raise


async def get_task(pool: asyncpg.Pool, task_id: str) -> dict[str, Any] | None:
    """Fetch a single task."""
    row = await pool.fetchrow("SELECT * FROM tasks WHERE task_id = $1::uuid", task_id)
    return dict(row) if row else None


async def update_task_status(
    pool: asyncpg.Pool,
    task_id: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Update task status and optionally set result."""
    if result is not None:
        import json

        await pool.execute(
            """
            UPDATE tasks SET status = $2, result = $3::jsonb, updated_at = now()
            WHERE task_id = $1::uuid
            """,
            task_id,
            status,
            json.dumps(result),
        )
    else:
        await pool.execute(
            "UPDATE tasks SET status = $2, updated_at = now() WHERE task_id = $1::uuid",
            task_id,
            status,
        )


async def update_task_status_if_match(
    pool: asyncpg.Pool,
    task_id: str,
    status: str,
    *,
    expected_status: str,
    result: dict[str, Any] | None = None,
) -> bool:
    """Update task status only when currently in expected_status.

    Returns True only when exactly one row was updated.
    """
    if result is not None:
        import json

        update_result = await pool.execute(
            """
            UPDATE tasks
            SET status = $2, result = $3::jsonb, updated_at = now()
            WHERE task_id = $1::uuid AND status = $4
            """,
            task_id,
            status,
            json.dumps(result),
            expected_status,
        )
    else:
        update_result = await pool.execute(
            """
            UPDATE tasks
            SET status = $2, updated_at = now()
            WHERE task_id = $1::uuid AND status = $3
            """,
            task_id,
            status,
            expected_status,
        )

    return int(update_result.split(" ", 1)[1].strip()) == 1


async def get_task_status(pool: asyncpg.Pool, task_id: str) -> str | None:
    """Fetch only the task status."""
    row = await pool.fetchval("SELECT status FROM tasks WHERE task_id = $1::uuid", task_id)
    if row is None:
        return None
    return str(row)


async def update_user_credits(pool: asyncpg.Pool, user_id: str, credits: int) -> None:
    """Mirror TB balance into PG users table (read-only cache)."""
    await pool.execute(
        "UPDATE users SET credits = $2 WHERE user_id = $1::uuid",
        user_id,
        credits,
    )
