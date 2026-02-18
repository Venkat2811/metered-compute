from __future__ import annotations

from pathlib import Path

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
)
from solution1.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_USER1_API_KEY,
    DEFAULT_USER2_API_KEY,
)
from solution1.db.migrate import (
    migration_directory,
    migration_template_values,
    ordered_migration_files,
    render_migration_sql,
)


def test_migration_files_are_ordered_and_stable() -> None:
    files = ordered_migration_files(migration_directory())
    assert [path.name for path in files] == [
        "0001_create_users_base.sql",
        "0002_extend_users_and_add_task_tables.sql",
        "0003_indexes.sql",
        "0004_seed_users.sql",
        "0005_idempotency_scope_and_reaper_index.sql",
        "0006_solution1_control_plane_tables.sql",
        "0007_reaper_retention_indexes.sql",
        "0008_webhook_delivery_tables.sql",
        "0009_token_revocations.sql",
    ]


def test_seed_template_renders_assignment_api_keys() -> None:
    seed_file = migration_directory() / "0004_seed_users.sql"
    sql = seed_file.read_text(encoding="utf-8")
    assert "{{ADMIN_API_KEY}}" in sql
    assert "{{ALICE_API_KEY}}" in sql
    assert "{{BOB_API_KEY}}" in sql
    assert "{{ADMIN_NAME}}" in sql
    assert "{{ALICE_NAME}}" in sql
    assert "{{BOB_NAME}}" in sql

    rendered = render_migration_sql(sql, migration_template_values())

    assert DEFAULT_ADMIN_API_KEY in rendered
    assert DEFAULT_USER1_API_KEY in rendered
    assert DEFAULT_USER2_API_KEY in rendered
    assert f"'{SEED_ADMIN_NAME}'" in rendered
    assert f"'{SEED_ALICE_NAME}'" in rendered
    assert f"'{SEED_BOB_NAME}'" in rendered
    assert str(SEED_ADMIN_CREDITS) in rendered
    assert str(SEED_TEST_USER1_CREDITS) in rendered
    assert str(SEED_TEST_USER2_CREDITS) in rendered
    assert f"'{ADMIN_ROLE}'" in rendered


def test_schema_template_renders_status_and_role_constraints() -> None:
    schema_file = migration_directory() / "0002_extend_users_and_add_task_tables.sql"
    sql = schema_file.read_text(encoding="utf-8")
    assert "{{DEFAULT_USER_ROLE}}" in sql
    assert "{{USER_ROLE_VALUES_SQL}}" in sql
    assert "{{DEFAULT_TASK_STATUS}}" in sql
    assert "{{TASK_STATUS_VALUES_SQL}}" in sql

    rendered = render_migration_sql(sql, migration_template_values())
    assert f"DEFAULT '{DEFAULT_USER_ROLE}'" in rendered
    assert f"role IN ({USER_ROLE_VALUES_SQL})" in rendered
    assert f"DEFAULT '{DEFAULT_TASK_STATUS}'" in rendered
    assert f"status IN ({TASK_STATUS_VALUES_SQL})" in rendered


def test_solution1_control_plane_template_renders_tier_constraints() -> None:
    control_plane_file = migration_directory() / "0006_solution1_control_plane_tables.sql"
    sql = control_plane_file.read_text(encoding="utf-8")
    assert "{{DEFAULT_TIER}}" in sql
    assert "{{ADMIN_TIER}}" in sql
    assert "{{TIER_VALUES_SQL}}" in sql

    rendered = render_migration_sql(sql, migration_template_values())
    assert f"DEFAULT '{DEFAULT_TIER}'" in rendered
    assert f"tier IN ({TIER_VALUES_SQL})" in rendered
    assert f"SET tier = '{ADMIN_TIER}'" in rendered
    assert "CREATE TABLE IF NOT EXISTS api_keys" in rendered
    assert "CREATE TABLE IF NOT EXISTS credit_drift_audit" in rendered
    assert "CREATE TABLE IF NOT EXISTS stream_checkpoints" in rendered


def test_all_migration_files_are_non_empty() -> None:
    for path in ordered_migration_files(migration_directory()):
        assert path.read_text(encoding="utf-8").strip()
        assert Path(path).suffix == ".sql"


def test_revocation_migration_defines_partitioned_table_and_partman_config() -> None:
    migration_file = migration_directory() / "0009_token_revocations.sql"
    sql = migration_file.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS token_revocations" in sql
    assert "PARTITION BY RANGE (revoked_at)" in sql
    assert "PRIMARY KEY (jti, revoked_at)" in sql
    assert "CREATE EXTENSION IF NOT EXISTS pg_partman" in sql
    assert "create_parent(" in sql
    assert "part_config" in sql
    assert "retention_keep_table = false" in sql
    assert "'2 days'" in sql
