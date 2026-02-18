from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast


class FakeTxContext:
    async def __aenter__(self) -> FakeTxContext:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeConnection:
    def transaction(self) -> FakeTxContext:
        return FakeTxContext()


class FakeAcquireContext:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self._connection = FakeConnection()

    def acquire(self) -> FakeAcquireContext:
        return FakeAcquireContext(self._connection)


class FakeRedisPipeline:
    def __init__(self, redis_client: FakeRedisClient) -> None:
        self._redis = redis_client
        self._ops: list[tuple[str, object, object]] = []

    async def __aenter__(self) -> FakeRedisPipeline:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def hset(self, key: str, mapping: dict[str, str]) -> FakeRedisPipeline:
        self._ops.append(("hset", key, dict(mapping)))
        return self

    def expire(self, key: str, ttl_seconds: int) -> FakeRedisPipeline:
        self._ops.append(("expire", key, ttl_seconds))
        return self

    async def execute(self) -> list[int | bool]:
        results: list[int | bool] = []
        for op_name, arg1, arg2 in self._ops:
            if op_name == "hset":
                result = await self._redis.hset(cast(str, arg1), cast(dict[str, str], arg2))
                results.append(result)
            elif op_name == "expire":
                result = await self._redis.expire(cast(str, arg1), cast(int, arg2))
                results.append(result)
        return results


@dataclass
class FakeRedisClient:
    hashes: dict[str, dict[str, str]] = field(default_factory=dict)
    values: dict[str, int] = field(default_factory=dict)
    sets: dict[str, set[str]] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)
    hset_calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    queue_depth: int = 0
    script_exists_values: list[int] | None = None
    fail_hgetall: bool = False
    fail_queue_depth: bool = False
    fail_script_exists: bool = False
    fail_cache_sync: bool = False

    @property
    def _hashes(self) -> dict[str, dict[str, str]]:
        """Backward-compatible alias for older tests."""

        return self.hashes

    @_hashes.setter
    def _hashes(self, new_hashes: dict[str, dict[str, str]]) -> None:
        self.hashes = new_hashes

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hset_calls.append((key, dict(mapping)))
        self.hashes[key] = dict(mapping)
        return 1

    async def expire(self, key: str, _: int) -> bool:
        return key in self.hashes

    async def delete(self, key: str) -> int:
        self.hashes.pop(key, None)
        self.values.pop(key, None)
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        if self.fail_hgetall:
            raise RuntimeError("redis unavailable")
        return self.hashes.get(key, {})

    async def llen(self, _: str) -> int:
        if self.fail_queue_depth:
            raise RuntimeError("queue unavailable")
        return self.queue_depth

    async def xlen(self, _: str) -> int:
        if self.fail_queue_depth:
            raise RuntimeError("queue unavailable")
        return self.queue_depth

    async def script_exists(self, *_: object) -> list[int]:
        if self.fail_script_exists:
            raise RuntimeError("script check failed")
        return self.script_exists_values or [1, 1]

    async def set(self, key: str, value: int) -> bool:
        if self.fail_cache_sync:
            raise RuntimeError("cache unavailable")
        self.values[key] = value
        return True

    async def sadd(self, key: str, value: str) -> int:
        if self.fail_cache_sync:
            raise RuntimeError("cache unavailable")
        bucket = self.sets.setdefault(key, set())
        bucket.add(value)
        return 1

    async def sismember(self, key: str, value: str) -> int:
        return 1 if value in self.sets.get(key, set()) else 0

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def rpush(self, key: str, value: str) -> int:
        queue = self.lists.setdefault(key, [])
        queue.append(value)
        return len(queue)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        queue = self.lists.get(key, [])
        length = len(queue)

        if length == 0:
            self.lists[key] = []
            return True

        start_idx = start if start >= 0 else length + start
        end_idx = end if end >= 0 else length + end

        start_idx = max(0, start_idx)
        end_idx = min(length - 1, end_idx)
        if end_idx < start_idx:
            self.lists[key] = []
            return True

        self.lists[key] = queue[start_idx : end_idx + 1]
        return True

    def pipeline(self, *, transaction: bool = False) -> FakeRedisPipeline:
        _ = transaction
        return FakeRedisPipeline(self)
