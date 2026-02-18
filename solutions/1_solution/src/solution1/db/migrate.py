from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Mapping
from pathlib import Path

import asyncpg

from solution1.constants import (
    ADMIN_ROLE,
    ADMIN_TIER,
    DEFAULT_TASK_STATUS,
    DEFAULT_TIER,
    DEFAULT_USER_ROLE,
    SEED_ADMIN_CREDITS,
    SEED_ADMIN_NAME,
    SEED_TEST_USER1_CREDITS,
    SEED_ALICE_NAME,
    SEED_TEST_USER2_CREDITS,
    SEED_BOB_NAME,
    TASK_STATUS_VALUES_SQL,
    TIER_VALUES_SQL,
    USER_ROLE_VALUES_SQL,
    UserRole,
)
from solution1.core.settings import load_settings

MIGRATION_FILENAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
MIGRATION_ADVISORY_LOCK_ID = 4_257_001


def migration_directory() -> Path:
    """Return the filesystem directory that stores SQL migration files."""

    return Path(__file__).resolve().parent / "migrations"


def ordered_migration_files(directory: Path) -> list[Path]:
    """Return lexicographically sorted migration files with strict name validation."""

    files = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix == ".sql")
    invalid = [path.name for path in files if not MIGRATION_FILENAME_RE.match(path.name)]
    if invalid:
        invalid_text = ", ".join(invalid)
        raise ValueError(f"invalid migration filename(s): {invalid_text}")
    return files


async def ensure_schema_migrations_table(connection: asyncpg.Connection) -> None:
    """Create migration tracking table if it is missing."""

    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version VARCHAR(255) PRIMARY KEY,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


async def applied_migration_versions(connection: asyncpg.Connection) -> set[str]:
    """Fetch already applied migration versions."""

    rows = await connection.fetch("SELECT version FROM schema_migrations")
    return {str(row["version"]) for row in rows}


def migration_template_values() -> dict[str, str]:
    """Return placeholder values used by schema and seed migrations."""

    settings = load_settings()
    return {
        "ADMIN_API_KEY": settings.admin_api_key,
        "ALICE_API_KEY": settings.alice_api_key,
        "BOB_API_KEY": settings.bob_api_key,
        "ADMIN_USER_ID": str(settings.oauth_admin_user_id),
        "TEST_USER1_USER_ID": str(settings.oauth_user1_user_id),
        "TEST_USER2_USER_ID": str(settings.oauth_user2_user_id),
        "ADMIN_ROLE": ADMIN_ROLE,
        "USER_ROLE": UserRole.USER.value,
        "DEFAULT_USER_ROLE": DEFAULT_USER_ROLE,
        "USER_ROLE_VALUES_SQL": USER_ROLE_VALUES_SQL,
        "DEFAULT_TIER": DEFAULT_TIER,
        "ADMIN_TIER": ADMIN_TIER,
        "TIER_VALUES_SQL": TIER_VALUES_SQL,
        "DEFAULT_TASK_STATUS": DEFAULT_TASK_STATUS,
        "TASK_STATUS_VALUES_SQL": TASK_STATUS_VALUES_SQL,
        "ADMIN_DEFAULT_CREDITS": str(SEED_ADMIN_CREDITS),
        "TEST_USER1_DEFAULT_CREDITS": str(SEED_TEST_USER1_CREDITS),
        "TEST_USER2_DEFAULT_CREDITS": str(SEED_TEST_USER2_CREDITS),
        "ADMIN_NAME": SEED_ADMIN_NAME,
        "ALICE_NAME": SEED_ALICE_NAME,
        "BOB_NAME": SEED_BOB_NAME,
    }


def render_migration_sql(sql: str, values: Mapping[str, str]) -> str:
    """Render known `{{KEY}}` placeholders in migration SQL."""

    rendered = sql
    for key, replacement in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
    return rendered


async def apply_pending_migrations(connection: asyncpg.Connection, directory: Path) -> list[str]:
    """Apply all pending SQL migration files in order."""

    await ensure_schema_migrations_table(connection)
    already_applied = await applied_migration_versions(connection)
    template_values = migration_template_values()
    applied_now: list[str] = []

    for migration_file in ordered_migration_files(directory):
        version = migration_file.name
        if version in already_applied:
            continue

        sql = render_migration_sql(
            migration_file.read_text(encoding="utf-8"), template_values
        ).strip()
        if not sql:
            raise ValueError(f"migration file is empty: {migration_file}")

        async with connection.transaction():
            await connection.execute(sql)
            await connection.execute(
                "INSERT INTO schema_migrations(version) VALUES($1)",
                version,
            )

        applied_now.append(version)

    return applied_now


async def run_migrations(dsn: str, directory: Path | None = None) -> list[str]:
    """Open a Postgres connection, apply pending migrations, and close cleanly."""

    resolved_directory = directory or migration_directory()
    connection = await asyncpg.connect(dsn=dsn)
    try:
        # Serialize migration execution across API/worker/reaper startup races.
        await connection.execute("SELECT pg_advisory_lock($1)", MIGRATION_ADVISORY_LOCK_ID)
        try:
            return await apply_pending_migrations(connection, resolved_directory)
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", MIGRATION_ADVISORY_LOCK_ID)
    finally:
        await connection.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply SQL migrations for Solution 1")
    parser.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN. When omitted, POSTGRES_DSN from settings is used.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    dsn = args.dsn or str(settings.postgres_dsn)

    applied = asyncio.run(run_migrations(dsn=dsn))
    if applied:
        print("Applied migrations:")
        for version in applied:
            print(f"- {version}")
    else:
        print("No pending migrations.")


if __name__ == "__main__":
    main()
