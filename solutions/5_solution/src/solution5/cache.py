"""Redis cache — task status and auth lookups."""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis
import structlog

log = structlog.get_logger()

AUTH_TTL = 300  # 5 min
TASK_TTL = 3600  # 1 hour


async def cache_auth(r: redis.Redis, api_key: str, user: dict[str, Any]) -> None:
    """Cache user info by API key hash."""
    await r.hset(f"auth:{api_key}", mapping={"user_id": str(user["user_id"]), "name": user["name"]})  # type: ignore[misc]
    await r.expire(f"auth:{api_key}", AUTH_TTL)


async def get_cached_auth(r: redis.Redis, api_key: str) -> dict[str, str] | None:
    """Get cached auth info."""
    data: dict[bytes, bytes] = await r.hgetall(f"auth:{api_key}")  # type: ignore[misc]
    return {k.decode(): v.decode() for k, v in data.items()} if data else None


async def cache_task(r: redis.Redis, task_id: str, task: dict[str, Any]) -> None:
    """Cache task data."""
    flat = {k: str(v) for k, v in task.items() if v is not None}
    await r.hset(f"task:{task_id}", mapping=flat)  # type: ignore[misc]
    await r.expire(f"task:{task_id}", TASK_TTL)


async def get_cached_task(r: redis.Redis, task_id: str) -> dict[str, str] | None:
    """Get cached task."""
    data: dict[bytes, bytes] = await r.hgetall(f"task:{task_id}")  # type: ignore[misc]
    return {k.decode(): v.decode() for k, v in data.items()} if data else None


async def invalidate_task(r: redis.Redis, task_id: str) -> None:
    """Remove task from cache."""
    await r.delete(f"task:{task_id}")
