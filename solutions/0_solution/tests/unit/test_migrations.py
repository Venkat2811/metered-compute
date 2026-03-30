from __future__ import annotations

from pathlib import Path

from solution0.constants import (
    ADMIN_ROLE,
    DEFAULT_TASK_STATUS,
    DEFAULT_USER_ROLE,
    SEED_ADMIN_CREDITS,
    SEED_ADMIN_NAME,
    SEED_ALICE_NAME,
    SEED_BOB_NAME,
    SEED_TEST_USER1_CREDITS,
    SEED_TEST_USER2_CREDITS,
    TASK_STATUS_VALUES_SQL,
    USER_ROLE_VALUES_SQL,
)
from solution0.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_BOB_API_KEY,
)
from solution0.db.migrate import (
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
    ]


def test_seed_template_renders_api_keys() -> None:
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
    assert DEFAULT_ALICE_API_KEY in rendered
    assert DEFAULT_BOB_API_KEY in rendered
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


def test_all_migration_files_are_non_empty() -> None:
    for path in ordered_migration_files(migration_directory()):
        assert path.read_text(encoding="utf-8").strip()
        assert Path(path).suffix == ".sql"
