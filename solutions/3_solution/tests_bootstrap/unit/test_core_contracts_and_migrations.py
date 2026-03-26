from __future__ import annotations

from pathlib import Path

import pytest

from solution3 import constants
from solution3.db import migrate


def test_runtime_constants_expose_sql_literals_and_defaults() -> None:
    assert constants.DEFAULT_USER_ROLE == "user"
    assert constants.ADMIN_ROLE == "admin"
    assert constants.ADMIN_TIER == "enterprise"
    assert constants.DEFAULT_REQUEST_MODE == "async"
    assert constants.DEFAULT_MODEL_CLASS == "small"
    assert constants.DEFAULT_TASK_STATUS == "PENDING"
    assert constants.DEFAULT_BILLING_STATE == "RESERVED"

    assert constants.USER_ROLE_VALUES_SQL == "'admin', 'user'"
    assert constants.TIER_VALUES_SQL == "'free', 'pro', 'enterprise'"
    assert constants.REQUEST_MODE_VALUES_SQL == "'async', 'sync', 'batch'"
    assert constants.MODEL_CLASS_VALUES_SQL == "'small', 'medium', 'large'"
    assert constants.TASK_STATUS_VALUES_SQL == (
        "'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED'"
    )
    assert constants.BILLING_STATE_VALUES_SQL == ("'RESERVED', 'CAPTURED', 'RELEASED', 'EXPIRED'")
    assert (
        frozenset(
            {
                constants.TaskStatus.COMPLETED.value,
                constants.TaskStatus.FAILED.value,
                constants.TaskStatus.CANCELLED.value,
                constants.TaskStatus.EXPIRED.value,
            }
        )
        == constants.TASK_TERMINAL_STATUSES
    )
    assert constants.TASK_EVENT_TYPES == (
        "task.requested",
        "task.started",
        "task.completed",
        "task.failed",
        "task.cancelled",
        "task.expired",
        "billing.captured",
        "billing.released",
    )


def test_migration_template_values_include_seed_and_enum_placeholders() -> None:
    values = migrate.migration_template_values()

    expected_keys = {
        "ADMIN_API_KEY",
        "ALICE_API_KEY",
        "BOB_API_KEY",
        "ADMIN_USER_ID",
        "TEST_USER1_USER_ID",
        "TEST_USER2_USER_ID",
        "ADMIN_ROLE",
        "USER_ROLE",
        "DEFAULT_USER_ROLE",
        "USER_ROLE_VALUES_SQL",
        "ADMIN_TIER",
        "DEFAULT_TIER",
        "TIER_VALUES_SQL",
        "DEFAULT_REQUEST_MODE",
        "REQUEST_MODE_VALUES_SQL",
        "DEFAULT_MODEL_CLASS",
        "MODEL_CLASS_VALUES_SQL",
        "DEFAULT_TASK_STATUS",
        "TASK_STATUS_VALUES_SQL",
        "DEFAULT_BILLING_STATE",
        "BILLING_STATE_VALUES_SQL",
        "ADMIN_NAME",
        "ALICE_NAME",
        "BOB_NAME",
        "ADMIN_DEFAULT_CREDITS",
        "TEST_USER1_DEFAULT_CREDITS",
        "TEST_USER2_DEFAULT_CREDITS",
    }

    assert expected_keys <= values.keys()
    assert values["DEFAULT_USER_ROLE"] == constants.DEFAULT_USER_ROLE
    assert values["ADMIN_ROLE"] == constants.ADMIN_ROLE
    assert values["DEFAULT_TIER"] == "free"
    assert values["DEFAULT_REQUEST_MODE"] == constants.RequestMode.ASYNC.value
    assert values["DEFAULT_MODEL_CLASS"] == constants.ModelClass.SMALL.value
    assert values["DEFAULT_TASK_STATUS"] == constants.TaskStatus.PENDING.value
    assert values["DEFAULT_BILLING_STATE"] == constants.BillingState.RESERVED.value


def test_render_migration_sql_replaces_all_known_placeholders() -> None:
    sql = """
    CREATE TABLE demo (
      role TEXT DEFAULT '{{DEFAULT_USER_ROLE}}',
      status TEXT DEFAULT '{{DEFAULT_TASK_STATUS}}',
      billing_state TEXT DEFAULT '{{DEFAULT_BILLING_STATE}}'
    );
    """

    rendered = migrate.render_migration_sql(sql, migrate.migration_template_values())

    assert "{{" not in rendered
    assert "user" in rendered
    assert "PENDING" in rendered
    assert "RESERVED" in rendered


def test_ordered_migration_files_reject_invalid_filenames(tmp_path: Path) -> None:
    (tmp_path / "0001_good_name.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "bad-name.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid migration filename"):
        migrate.ordered_migration_files(tmp_path)


def test_rendered_migrations_cover_rfc_storage_tables() -> None:
    rendered_sql = "\n".join(
        migrate.render_migration_sql(
            path.read_text(encoding="utf-8"), migrate.migration_template_values()
        )
        for path in migrate.ordered_migration_files(migrate.migration_directory())
    )

    assert "{{" not in rendered_sql
    assert "CREATE SCHEMA IF NOT EXISTS cmd;" in rendered_sql
    assert "CREATE SCHEMA IF NOT EXISTS query;" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.users" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.api_keys" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.task_commands" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.outbox_events" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.inbox_events" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.projection_checkpoints" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS cmd.billing_reconcile_jobs" in rendered_sql
    assert "CREATE TABLE IF NOT EXISTS query.task_query_view" in rendered_sql
    assert "cmd.task_events" not in rendered_sql
