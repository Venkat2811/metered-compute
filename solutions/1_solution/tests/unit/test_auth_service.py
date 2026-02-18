from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from redis.exceptions import ConnectionError

from solution1.constants import UserRole
from solution1.models.domain import AuthUser
from solution1.services.auth import resolve_user_from_api_key, revoke_jti
from tests.constants import TEST_DB_USER_NAME, TEST_USER_ID, TEST_USER_ID_STR, TEST_USER_NAME


@dataclass
class _FakeRedis:
    cache: dict[str, dict[str, str]]
    fail_reads: bool = False
    fail_writes: bool = False

    async def hgetall(self, key: str) -> dict[str, str]:
        if self.fail_reads:
            raise ConnectionError("redis down")
        return self.cache.get(key, {})

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        if self.fail_writes:
            raise ConnectionError("redis down")
        self.cache[key] = mapping
        return 1

    async def expire(self, *_: object) -> bool:
        if self.fail_writes:
            raise ConnectionError("redis down")
        return True

    async def setnx(self, *_: object) -> bool:
        if self.fail_writes:
            raise ConnectionError("redis down")
        return True


class _FakeMetricLabel:
    def __init__(self, counts: dict[str, int], result: str) -> None:
        self._counts = counts
        self._result = result

    def inc(self) -> None:
        self._counts[self._result] = self._counts.get(self._result, 0) + 1


class _FakeMetricCounter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def labels(self, *, result: str) -> _FakeMetricLabel:
        return _FakeMetricLabel(self.counts, result)


@dataclass
class _FakeRevocationRedis:
    set_members: dict[str, set[str]]
    expiries: dict[str, int]

    async def sadd(self, key: str, member: str) -> int:
        self.set_members.setdefault(key, set()).add(member)
        return 1

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self.expiries[key] = ttl_seconds
        return True


class _FailingRevocationRedis:
    async def sadd(self, *_: object, **__: object) -> int:
        raise ConnectionError("redis unavailable")

    async def expire(self, *_: object, **__: object) -> bool:
        raise AssertionError("expire should not run when sadd fails")


@pytest.mark.asyncio
async def test_resolve_user_cache_hit_skips_db(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = _FakeRedis(
        cache={
            "auth:api-key": {
                "user_id": TEST_USER_ID_STR,
                "name": TEST_USER_NAME,
                "role": "user",
            }
        }
    )

    async def fake_fetch_user_by_api_key(*_: object, **__: object) -> None:
        raise AssertionError("db should not be queried on cache hit")

    monkeypatch.setattr("solution1.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user == AuthUser(
        api_key="api-key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=0,
    )


@pytest.mark.asyncio
async def test_resolve_user_falls_back_to_db_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis(cache={}, fail_reads=True)

    async def fake_fetch_user_by_api_key(*_: object, **__: object) -> AuthUser:
        return AuthUser(
            api_key="api-key",
            user_id=TEST_USER_ID,
            name=TEST_DB_USER_NAME,
            role=UserRole.ADMIN,
            credits=321,
        )

    monkeypatch.setattr("solution1.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user is not None
    assert user.name == TEST_DB_USER_NAME
    assert user.role == UserRole.ADMIN


@pytest.mark.asyncio
async def test_resolve_user_tolerates_cache_population_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis(cache={}, fail_writes=True)

    async def fake_fetch_user_by_api_key(*_: object, **__: object) -> AuthUser:
        return AuthUser(
            api_key="api-key",
            user_id=TEST_USER_ID,
            name=TEST_DB_USER_NAME,
            role=UserRole.USER,
            credits=50,
        )

    monkeypatch.setattr("solution1.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user is not None
    assert user.credits == 50
    if "auth:api-key" in redis_client.cache:
        assert "credits" not in redis_client.cache["auth:api-key"]


@pytest.mark.asyncio
async def test_resolve_user_falls_back_to_db_when_auth_cache_schema_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis(
        cache={
            "auth:api-key": {
                "user_id": TEST_USER_ID_STR,
                "name": "test",
            }
        }
    )

    async def fake_fetch_user_by_api_key(*_: object, **__: object) -> AuthUser:
        return AuthUser(
            api_key="api-key",
            user_id=TEST_USER_ID,
            name=TEST_DB_USER_NAME,
            role=UserRole.USER,
            credits=123,
        )

    monkeypatch.setattr("solution1.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user is not None
    assert user.name == TEST_DB_USER_NAME


@pytest.mark.asyncio
async def test_schema_miss_metric_does_not_double_count_as_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis(
        cache={
            "auth:api-key": {
                "user_id": TEST_USER_ID_STR,
                "name": TEST_USER_NAME,
            }
        }
    )
    metrics = _FakeMetricCounter()

    async def fake_fetch_user_by_api_key(*_: object, **__: object) -> AuthUser:
        return AuthUser(
            api_key="api-key",
            user_id=TEST_USER_ID,
            name=TEST_DB_USER_NAME,
            role=UserRole.USER,
            credits=123,
        )

    monkeypatch.setattr("solution1.services.auth.AUTH_CACHE_RESULTS_TOTAL", metrics)
    monkeypatch.setattr("solution1.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user is not None
    assert metrics.counts.get("schema_miss", 0) == 1
    assert metrics.counts.get("miss", 0) == 0


@pytest.mark.asyncio
async def test_empty_auth_cache_counts_only_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis(cache={})
    metrics = _FakeMetricCounter()

    async def fake_fetch_user_by_api_key(*_: object, **__: object) -> AuthUser:
        return AuthUser(
            api_key="api-key",
            user_id=TEST_USER_ID,
            name=TEST_DB_USER_NAME,
            role=UserRole.USER,
            credits=42,
        )

    monkeypatch.setattr("solution1.services.auth.AUTH_CACHE_RESULTS_TOTAL", metrics)
    monkeypatch.setattr("solution1.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user is not None
    assert metrics.counts.get("miss", 0) == 1
    assert metrics.counts.get("schema_miss", 0) == 0


@pytest.mark.asyncio
async def test_revoke_jti_dual_writes_redis_then_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRevocationRedis(set_members={}, expiries={})
    calls: list[tuple[str, object, object]] = []
    expires_at = datetime.now(tz=UTC) + timedelta(hours=24)

    async def fake_insert_revoked_jti(
        executor: object,
        *,
        jti: str,
        user_id: object,
        expires_at: datetime,
    ) -> None:
        calls.append((jti, user_id, expires_at))
        _ = executor

    monkeypatch.setattr("solution1.services.auth.insert_revoked_jti", fake_insert_revoked_jti)

    await revoke_jti(
        redis_client=cast(Any, redis_client),
        pool=cast(Any, object()),
        user_id=TEST_USER_ID,
        jti="jti-revoke-1",
        expires_at=expires_at,
        bucket_ttl=129600,
    )

    today = datetime.now(tz=UTC).date().isoformat()
    expected_key = f"revoked:{TEST_USER_ID}:{today}"
    assert redis_client.set_members[expected_key] == {"jti-revoke-1"}
    assert redis_client.expiries[expected_key] == 129600
    assert calls == [("jti-revoke-1", TEST_USER_ID, expires_at)]


@pytest.mark.asyncio
async def test_revoke_jti_persists_to_postgres_when_redis_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FailingRevocationRedis()
    calls: list[tuple[str, object, object]] = []
    expires_at = datetime.now(tz=UTC) + timedelta(hours=24)

    async def fake_insert_revoked_jti(
        executor: object,
        *,
        jti: str,
        user_id: object,
        expires_at: datetime,
    ) -> None:
        calls.append((jti, user_id, expires_at))
        _ = executor

    monkeypatch.setattr("solution1.services.auth.insert_revoked_jti", fake_insert_revoked_jti)

    await revoke_jti(
        redis_client=cast(Any, redis_client),
        pool=cast(Any, object()),
        user_id=TEST_USER_ID,
        jti="jti-revoke-2",
        expires_at=expires_at,
        bucket_ttl=129600,
    )

    assert calls == [("jti-revoke-2", TEST_USER_ID, expires_at)]
