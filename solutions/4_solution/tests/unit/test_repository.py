"""Unit tests for Postgres repository module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg.exceptions
import pytest

from solution4 import repository


class TestGetUserByApiKey:
    @pytest.mark.asyncio
    async def test_returns_user_dict(self) -> None:
        row = {"user_id": "abc-123", "name": "alice", "credits": 100}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)
        result = await repository.get_user_by_api_key(pool, "sk-test-key")
        assert result == row

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_key(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        result = await repository.get_user_by_api_key(pool, "sk-bad-key")
        assert result is None


class TestGetUserById:
    @pytest.mark.asyncio
    async def test_returns_user_dict(self) -> None:
        row = {"user_id": "abc-123", "name": "alice", "credits": 100, "role": "admin"}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)

        result = await repository.get_user_by_id(pool, "abc-123")

        assert result == row

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_user(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await repository.get_user_by_id(pool, "missing-user")

        assert result is None


class TestCreateTask:
    @pytest.mark.asyncio
    async def test_creates_and_returns_task(self) -> None:
        row = {
            "task_id": "t-1",
            "user_id": "abc-123",
            "status": "PENDING",
            "x": 3,
            "y": 4,
            "cost": 10,
            "tb_transfer_id": "deadbeef",
        }
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)
        result = await repository.create_task(
            pool,
            task_id="t-1",
            user_id="abc-123",
            x=3,
            y=4,
            cost=10,
            tb_transfer_id="deadbeef",
        )
        assert result == row
        pool.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotency_replay_returns_existing(self) -> None:
        existing_row = {
            "task_id": "t-original",
            "user_id": "abc-123",
            "status": "COMPLETED",
            "x": 3,
            "y": 4,
            "cost": 10,
            "tb_transfer_id": "deadbeef",
        }
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                asyncpg.exceptions.UniqueViolationError("duplicate key"),
                existing_row,
            ]
        )
        result = await repository.create_task(
            pool,
            task_id="t-2",
            user_id="abc-123",
            x=3,
            y=4,
            cost=10,
            tb_transfer_id="deadbeef2",
            idempotency_key="idem-1",
        )
        assert result == existing_row
        assert pool.fetchrow.await_count == 2

    @pytest.mark.asyncio
    async def test_unique_violation_without_idempotency_key_raises(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=asyncpg.exceptions.UniqueViolationError("duplicate key"))
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await repository.create_task(
                pool,
                task_id="t-dup",
                user_id="abc-123",
                x=3,
                y=4,
                cost=10,
                tb_transfer_id="deadbeef3",
            )

    @pytest.mark.asyncio
    async def test_idempotency_replay_no_match_raises(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                asyncpg.exceptions.UniqueViolationError("duplicate key"),
                None,  # no matching existing row
            ]
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await repository.create_task(
                pool,
                task_id="t-3",
                user_id="abc-123",
                x=3,
                y=4,
                cost=10,
                tb_transfer_id="deadbeef4",
                idempotency_key="idem-orphan",
            )


class TestGetTask:
    @pytest.mark.asyncio
    async def test_returns_task_dict(self) -> None:
        row = {"task_id": "t-1", "status": "COMPLETED"}
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)
        result = await repository.get_task(pool, "t-1")
        assert result == row

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_task(self) -> None:
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        result = await repository.get_task(pool, "t-missing")
        assert result is None


class TestUpdateTaskStatus:
    @pytest.mark.asyncio
    async def test_updates_status_without_result(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        await repository.update_task_status(pool, "t-1", "COMPLETED")
        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args[0]
        assert "t-1" in call_args
        assert "COMPLETED" in call_args

    @pytest.mark.asyncio
    async def test_updates_status_with_result(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        result = {"sum": 7, "product": 12}
        await repository.update_task_status(pool, "t-1", "COMPLETED", result=result)
        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args[0]
        assert "t-1" in call_args
        assert "COMPLETED" in call_args


class TestUpdateTaskStatusIfMatch:
    @pytest.mark.asyncio
    async def test_updates_status_if_expected_matches(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        updated = await repository.update_task_status_if_match(
            pool,
            task_id="t-1",
            status="RUNNING",
            expected_status="PENDING",
        )
        assert updated is True
        pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_update_if_expected_mismatch(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock(return_value="UPDATE 0")
        updated = await repository.update_task_status_if_match(
            pool,
            task_id="t-1",
            status="COMPLETED",
            expected_status="RUNNING",
            result={"sum": 1, "product": 2},
        )
        assert updated is False
        pool.execute.assert_awaited_once()


class TestUpdateUserCredits:
    @pytest.mark.asyncio
    async def test_mirrors_balance_to_pg(self) -> None:
        pool = MagicMock()
        pool.execute = AsyncMock()
        await repository.update_user_credits(pool, "abc-123", 500)
        pool.execute.assert_awaited_once()


class TestRunMigrations:
    @pytest.mark.asyncio
    async def test_applies_sql_files_in_order(self) -> None:
        import tempfile
        from pathlib import Path

        conn = MagicMock()
        conn.execute = AsyncMock()
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir)
            (migrations_dir / "0001_schema.sql").write_text("CREATE TABLE test();")
            (migrations_dir / "0002_seed.sql").write_text("INSERT INTO test VALUES();")

            await repository.run_migrations(pool, migrations_dir=str(migrations_dir))
            assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_duplicate_table_error(self) -> None:
        import tempfile
        from pathlib import Path

        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=asyncpg.exceptions.DuplicateTableError("already exists"))
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir)
            (migrations_dir / "0001_schema.sql").write_text("CREATE TABLE test();")

            # Should not raise — DuplicateTableError is silently skipped
            await repository.run_migrations(pool, migrations_dir=str(migrations_dir))
            conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_unique_violation_in_seed(self) -> None:
        import tempfile
        from pathlib import Path

        conn = MagicMock()
        conn.execute = AsyncMock(side_effect=asyncpg.exceptions.UniqueViolationError("duplicate key"))
        pool = MagicMock()
        pool.acquire = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            migrations_dir = Path(tmpdir)
            (migrations_dir / "0002_seed.sql").write_text("INSERT INTO test VALUES();")

            # Should not raise — UniqueViolationError is silently skipped
            await repository.run_migrations(pool, migrations_dir=str(migrations_dir))
            conn.execute.assert_awaited_once()
