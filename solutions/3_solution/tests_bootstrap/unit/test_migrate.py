from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from solution3.db import migrate


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        _ = (exc_type, exc, tb)


class FakeConnection:
    def __init__(self, *, applied_versions: list[str] | None = None) -> None:
        self.applied_versions = list(applied_versions or [])
        self.fetch_calls: list[str] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetch(self, query: str) -> list[dict[str, str]]:
        self.fetch_calls.append(query)
        return [{"version": version} for version in self.applied_versions]

    async def execute(self, query: str, *args: object) -> str:
        self.execute_calls.append((query, args))
        return "OK"

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def close(self) -> None:
        self.closed = True


def test_ordered_migration_files_sorts_and_rejects_invalid_names(tmp_path: Path) -> None:
    (tmp_path / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")

    assert [path.name for path in migrate.ordered_migration_files(tmp_path)] == [
        "0001_first.sql",
        "0002_second.sql",
    ]

    (tmp_path / "bad-name.sql").write_text("SELECT 3;", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid migration filename"):
        migrate.ordered_migration_files(tmp_path)


def test_render_and_load_migration_sql_validate_placeholders(tmp_path: Path) -> None:
    migration_file = tmp_path / "0001_test.sql"
    migration_file.write_text("SELECT '{{VALUE}}';", encoding="utf-8")

    assert migrate.render_migration_sql("{{VALUE}}", {"VALUE": "ok"}) == "ok"
    assert migrate.load_migration_sql(migration_file, {"VALUE": "ok"}) == "SELECT 'ok';"

    migration_file.write_text("   ", encoding="utf-8")
    with pytest.raises(ValueError, match="migration file is empty"):
        migrate.load_migration_sql(migration_file, {"VALUE": "ok"})

    migration_file.write_text("SELECT '{{MISSING}}';", encoding="utf-8")
    with pytest.raises(ValueError, match="unresolved migration placeholders"):
        migrate.load_migration_sql(migration_file, {"VALUE": "ok"})


def test_migration_template_values_use_settings() -> None:
    settings = SimpleNamespace(
        admin_api_key="admin-key",
        alice_api_key="user1-key",
        bob_api_key="user2-key",
        oauth_admin_user_id="admin-user",
        oauth_user1_user_id="user1-user",
        oauth_user2_user_id="user2-user",
    )

    values = migrate.migration_template_values(cast(Any, settings))

    assert values["ADMIN_API_KEY"] == "admin-key"
    assert values["ALICE_API_KEY"] == "user1-key"
    assert values["ADMIN_USER_ID"] == "admin-user"
    assert values["ADMIN_NAME"] == "admin"


@pytest.mark.asyncio
async def test_apply_pending_migrations_runs_only_missing_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = FakeConnection(applied_versions=["0001_first.sql"])
    directory = tmp_path
    (directory / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (directory / "0002_second.sql").write_text("SELECT 2;", encoding="utf-8")

    monkeypatch.setattr(migrate, "migration_template_values", lambda settings=None: {"VALUE": "ok"})

    applied = await migrate.apply_pending_migrations(connection, directory)

    assert applied == ["0002_second.sql"]
    executed_sql = [query for query, _args in connection.execute_calls]
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in query for query in executed_sql)
    assert any("SELECT 2;" in query for query in executed_sql)
    assert any("INSERT INTO schema_migrations" in query for query in executed_sql)


@pytest.mark.asyncio
async def test_run_migrations_acquires_lock_applies_and_unlocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = FakeConnection()

    async def fake_connect(*, dsn: str) -> FakeConnection:
        assert dsn == "postgresql://db"
        return connection

    async def fake_apply_pending_migrations(conn: FakeConnection, directory: Path) -> list[str]:
        assert conn is connection
        assert directory == tmp_path
        return ["0001_test.sql"]

    monkeypatch.setattr("solution3.db.migrate.asyncpg.connect", fake_connect)
    monkeypatch.setattr(migrate, "apply_pending_migrations", fake_apply_pending_migrations)

    applied = await migrate.run_migrations("postgresql://db", directory=tmp_path)

    assert applied == ["0001_test.sql"]
    assert connection.execute_calls[0] == (
        "SELECT pg_advisory_lock($1)",
        (migrate.MIGRATION_ADVISORY_LOCK_ID,),
    )
    assert connection.execute_calls[1] == (
        "SELECT pg_advisory_unlock($1)",
        (migrate.MIGRATION_ADVISORY_LOCK_ID,),
    )
    assert connection.closed is True


def test_main_prints_applied_or_no_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(migrate, "_parse_args", lambda: SimpleNamespace(dsn="postgresql://db"))
    monkeypatch.setattr(
        migrate, "load_settings", lambda: SimpleNamespace(postgres_dsn="postgresql://ignored")
    )

    def fake_asyncio_run(coro: object) -> list[str]:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return ["0001_test.sql", "0002_test.sql"]

    monkeypatch.setattr("solution3.db.migrate.asyncio.run", fake_asyncio_run)

    migrate.main()

    stdout = capsys.readouterr().out
    assert "Applied migrations:" in stdout
    assert "- 0001_test.sql" in stdout
    assert "- 0002_test.sql" in stdout
