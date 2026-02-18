from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from solution1.db.repository import insert_revoked_jti, is_jti_revoked, load_active_revoked_jtis


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, *args: object) -> str:
        self.calls.append((query, args))
        return "INSERT 0 1"


class _FakePool:
    def __init__(
        self,
        *,
        fetchval_result: object = None,
        fetch_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.fetchval_result = fetchval_result
        self.fetch_rows = fetch_rows or []
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchval(self, query: str, *args: object) -> object:
        self.fetchval_calls.append((query, args))
        return self.fetchval_result

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        return self.fetch_rows


@pytest.mark.asyncio
async def test_insert_revoked_jti_writes_expected_sql_shape() -> None:
    executor = _FakeExecutor()
    user_id = UUID("47b47338-5355-4edc-860b-846d71a2a75a")
    expires_at = datetime.now(tz=UTC) + timedelta(hours=24)

    await insert_revoked_jti(
        executor,
        jti="jti-123",
        user_id=user_id,
        expires_at=expires_at,
    )

    assert len(executor.calls) == 1
    query, args = executor.calls[0]
    assert "INSERT INTO token_revocations" in query
    assert args == ("jti-123", user_id, expires_at)


@pytest.mark.asyncio
async def test_is_jti_revoked_uses_exists_lookup() -> None:
    pool = _FakePool(fetchval_result=True)

    revoked = await is_jti_revoked(pool, jti="revoked-jti")

    assert revoked is True
    assert len(pool.fetchval_calls) == 1
    query, args = pool.fetchval_calls[0]
    assert "FROM token_revocations" in query
    assert args == ("revoked-jti",)


@pytest.mark.asyncio
async def test_load_active_revoked_jtis_returns_typed_rows() -> None:
    pool = _FakePool(
        fetch_rows=[
            {
                "jti": "jti-a",
                "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
                "day_iso": "2026-02-17",
            },
            {
                "jti": "jti-b",
                "user_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "day_iso": "2026-02-16",
            },
        ]
    )
    since = datetime.now(tz=UTC) - timedelta(days=1)

    rows = await load_active_revoked_jtis(pool, since=since)

    assert rows == [
        ("jti-a", UUID("47b47338-5355-4edc-860b-846d71a2a75a"), "2026-02-17"),
        ("jti-b", UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), "2026-02-16"),
    ]
    assert len(pool.fetch_calls) == 1
    query, args = pool.fetch_calls[0]
    assert "WHERE revoked_at >= $1" in query
    assert args == (since,)
