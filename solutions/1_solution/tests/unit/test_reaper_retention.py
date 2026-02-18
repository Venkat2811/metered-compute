from __future__ import annotations

import pytest

from solution1.db import repository


class _FakePool:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, *params: object) -> str:
        self.queries.append((sql.strip(), params))
        # simulate PG rowcount tag format
        return "DELETE 3"


@pytest.mark.asyncio
async def test_purge_old_credit_transactions_executes_bounded_batch_delete() -> None:
    pool = _FakePool()
    deleted = await repository.purge_old_credit_transactions(
        pool,
        older_than_seconds=86400,
        batch_size=500,
    )

    assert deleted == 3
    assert pool.queries, "expected one repository delete query"
    sql, params = pool.queries[-1]
    assert "DELETE FROM credit_transactions" in sql
    assert "ORDER BY created_at ASC" in sql
    assert "LIMIT $2" in sql
    assert len(params) == 2
    assert params[1] == 500


@pytest.mark.asyncio
async def test_purge_old_credit_drift_audit_executes_bounded_batch_delete() -> None:
    pool = _FakePool()
    deleted = await repository.purge_old_credit_drift_audit(
        pool,
        older_than_seconds=3600,
        batch_size=250,
    )

    assert deleted == 3
    assert pool.queries, "expected one repository delete query"
    sql, params = pool.queries[-1]
    assert "DELETE FROM credit_drift_audit" in sql
    assert "ORDER BY checked_at ASC" in sql
    assert "LIMIT $2" in sql
    assert params[1] == 250


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "older_than_seconds,batch_size",
    [
        (0, 500),
        (86400, 0),
    ],
)
async def test_purge_functions_noop_for_non_positive_windows_or_batch(
    older_than_seconds: int, batch_size: int
) -> None:
    pool = _FakePool()
    for fn in (
        repository.purge_old_credit_transactions,
        repository.purge_old_credit_drift_audit,
    ):
        deleted = await fn(pool, older_than_seconds=older_than_seconds, batch_size=batch_size)
        assert deleted == 0
    assert not pool.queries
