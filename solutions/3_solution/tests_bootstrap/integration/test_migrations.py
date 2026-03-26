from __future__ import annotations

import asyncio
import os
import subprocess

import asyncpg
import pytest


def _dsn() -> str:
    return os.environ.get(
        "SOLUTION3_TEST_POSTGRES_DSN",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )


async def _reset_database(dsn: str) -> None:
    connection = await asyncpg.connect(dsn=dsn)
    try:
        await connection.execute("DROP SCHEMA IF EXISTS query CASCADE;")
        await connection.execute("DROP SCHEMA IF EXISTS cmd CASCADE;")
        await connection.execute("DROP TABLE IF EXISTS schema_migrations CASCADE;")
    finally:
        await connection.close()


async def _fetch_core_state(dsn: str) -> tuple[set[tuple[str, str]], int]:
    connection = await asyncpg.connect(dsn=dsn)
    try:
        rows = await connection.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE (table_schema, table_name) IN (
              ('cmd', 'users'),
              ('cmd', 'api_keys'),
              ('cmd', 'task_commands'),
              ('cmd', 'outbox_events'),
              ('cmd', 'inbox_events'),
              ('cmd', 'projection_checkpoints'),
              ('cmd', 'billing_reconcile_jobs'),
              ('query', 'task_query_view')
            )
            """
        )
        seeded_users = await connection.fetchval("SELECT COUNT(*) FROM cmd.users")
    finally:
        await connection.close()

    return {(str(row["table_schema"]), str(row["table_name"])) for row in rows}, int(seeded_users)


@pytest.mark.integration
def test_migrate_script_creates_core_tables_from_clean_database() -> None:
    dsn = _dsn()
    asyncio.run(_reset_database(dsn))

    env = os.environ | {"SOLUTION3_MIGRATE_DSN": dsn}
    first_run = subprocess.run(
        ["./scripts/migrate.sh"],
        cwd=".",
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    second_run = subprocess.run(
        ["./scripts/migrate.sh"],
        cwd=".",
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    tables, seeded_users = asyncio.run(_fetch_core_state(dsn))

    assert {
        ("cmd", "users"),
        ("cmd", "api_keys"),
        ("cmd", "task_commands"),
        ("cmd", "outbox_events"),
        ("cmd", "inbox_events"),
        ("cmd", "projection_checkpoints"),
        ("cmd", "billing_reconcile_jobs"),
        ("query", "task_query_view"),
    } <= tables
    assert seeded_users == 3
    assert "Applied migrations:" in first_run.stdout
    assert "No pending migrations." in second_run.stdout
