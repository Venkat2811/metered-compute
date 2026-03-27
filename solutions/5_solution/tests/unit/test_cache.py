"""Unit tests for Redis cache module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from solution5 import cache


def _make_redis_mock() -> MagicMock:
    """Create a mock Redis client with async methods."""
    r = MagicMock()
    r.hset = AsyncMock(return_value=1)
    r.hgetall = AsyncMock(return_value={})
    r.expire = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    return r


class TestCacheAuth:
    @pytest.mark.asyncio
    async def test_cache_auth_sets_hash_and_ttl(self) -> None:
        r = _make_redis_mock()
        user: dict[str, Any] = {"user_id": "abc-123", "name": "alice"}
        await cache.cache_auth(r, "sk-test-key", user)
        r.hset.assert_awaited_once_with(
            "auth:sk-test-key",
            mapping={"user_id": "abc-123", "name": "alice", "role": "user"},
        )
        r.expire.assert_awaited_once_with("auth:sk-test-key", cache.AUTH_TTL)

    @pytest.mark.asyncio
    async def test_get_cached_auth_hit(self) -> None:
        r = _make_redis_mock()
        r.hgetall = AsyncMock(return_value={b"user_id": b"abc-123", b"name": b"alice"})
        result = await cache.get_cached_auth(r, "sk-test-key")
        assert result == {"user_id": "abc-123", "name": "alice"}

    @pytest.mark.asyncio
    async def test_get_cached_auth_miss(self) -> None:
        r = _make_redis_mock()
        r.hgetall = AsyncMock(return_value={})
        result = await cache.get_cached_auth(r, "sk-missing")
        assert result is None


class TestCacheTask:
    @pytest.mark.asyncio
    async def test_cache_task_sets_hash_and_ttl(self) -> None:
        r = _make_redis_mock()
        task: dict[str, Any] = {"task_id": "t-1", "status": "PENDING", "x": 3, "result": None}
        await cache.cache_task(r, "t-1", task)
        # result=None should be filtered out
        call_args = r.hset.call_args
        mapping = call_args.kwargs.get("mapping") or call_args[1].get("mapping")
        assert "result" not in mapping
        assert mapping["task_id"] == "t-1"
        assert mapping["status"] == "PENDING"
        r.expire.assert_awaited_once_with("task:t-1", cache.TASK_TTL)

    @pytest.mark.asyncio
    async def test_cache_task_serializes_structured_result_as_json(self) -> None:
        r = _make_redis_mock()
        task: dict[str, Any] = {
            "task_id": "t-1",
            "status": "COMPLETED",
            "result": {"sum": 7, "product": 12},
        }

        await cache.cache_task(r, "t-1", task)

        mapping = r.hset.call_args.kwargs["mapping"]
        assert mapping["result"] == '{"sum":7,"product":12}'

    @pytest.mark.asyncio
    async def test_get_cached_task_hit(self) -> None:
        r = _make_redis_mock()
        r.hgetall = AsyncMock(return_value={b"task_id": b"t-1", b"status": b"COMPLETED"})
        result = await cache.get_cached_task(r, "t-1")
        assert result == {"task_id": "t-1", "status": "COMPLETED"}

    @pytest.mark.asyncio
    async def test_get_cached_task_decodes_structured_result(self) -> None:
        r = _make_redis_mock()
        r.hgetall = AsyncMock(
            return_value={
                b"task_id": b"t-1",
                b"status": b"COMPLETED",
                b"result": b'{"sum":7,"product":12}',
            }
        )

        result = await cache.get_cached_task(r, "t-1")

        assert result == {
            "task_id": "t-1",
            "status": "COMPLETED",
            "result": {"sum": 7, "product": 12},
        }

    @pytest.mark.asyncio
    async def test_get_cached_task_miss(self) -> None:
        r = _make_redis_mock()
        r.hgetall = AsyncMock(return_value={})
        result = await cache.get_cached_task(r, "t-missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_invalidate_task_deletes_key(self) -> None:
        r = _make_redis_mock()
        await cache.invalidate_task(r, "t-1")
        r.delete.assert_awaited_once_with("task:t-1")
