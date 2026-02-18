from __future__ import annotations

from dataclasses import dataclass

import pytest
from redis.exceptions import ConnectionError

from solution0.constants import UserRole
from solution0.models.domain import AuthUser
from solution0.services.auth import resolve_user_from_api_key
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

    monkeypatch.setattr("solution0.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

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

    monkeypatch.setattr("solution0.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

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

    monkeypatch.setattr("solution0.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

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

    monkeypatch.setattr("solution0.services.auth.fetch_user_by_api_key", fake_fetch_user_by_api_key)

    user = await resolve_user_from_api_key(
        api_key="api-key",
        redis_client=redis_client,  # type: ignore[arg-type]
        db_pool=object(),
        auth_cache_ttl_seconds=60,
    )

    assert user is not None
    assert user.name == TEST_DB_USER_NAME
