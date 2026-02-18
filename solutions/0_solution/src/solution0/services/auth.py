from __future__ import annotations

from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import RedisError

from solution0.constants import UserRole
from solution0.db.repository import fetch_user_by_api_key
from solution0.models.domain import AuthUser
from solution0.observability.metrics import AUTH_CACHE_RESULTS_TOTAL, AUTH_DB_LOOKUPS_TOTAL


def _auth_cache_key(api_key: str) -> str:
    return f"auth:{api_key}"


def _credits_key(user_id: UUID) -> str:
    return f"credits:{user_id}"


async def resolve_user_from_api_key(
    *,
    api_key: str,
    redis_client: Redis[str],
    db_pool: asyncpg.Pool,
    auth_cache_ttl_seconds: int,
) -> AuthUser | None:
    """Resolve authenticated user through Redis cache-aside with Postgres fallback."""

    cache_available = True
    try:
        cached = await redis_client.hgetall(_auth_cache_key(api_key))
    except RedisError:
        AUTH_CACHE_RESULTS_TOTAL.labels(result="error").inc()
        cached = {}
        cache_available = False

    if cached:
        if all(field in cached for field in ("user_id", "name", "role")):
            try:
                role = UserRole(cached["role"])
            except ValueError:
                role = None
            if role is not None:
                AUTH_CACHE_RESULTS_TOTAL.labels(result="hit").inc()
                return AuthUser(
                    api_key=api_key,
                    user_id=UUID(cached["user_id"]),
                    name=cached["name"],
                    role=role,
                    credits=0,
                )
        AUTH_CACHE_RESULTS_TOTAL.labels(result="schema_miss").inc()
    if cache_available:
        AUTH_CACHE_RESULTS_TOTAL.labels(result="miss").inc()

    user = await fetch_user_by_api_key(db_pool, api_key)
    if user is None:
        AUTH_DB_LOOKUPS_TOTAL.labels(result="not_found").inc()
        return None
    AUTH_DB_LOOKUPS_TOTAL.labels(result="found").inc()

    if cache_available:
        try:
            await redis_client.hset(
                _auth_cache_key(api_key),
                mapping={
                    "user_id": str(user.user_id),
                    "name": user.name,
                    "role": str(user.role),
                },
            )
            await redis_client.expire(_auth_cache_key(api_key), auth_cache_ttl_seconds)
            await redis_client.setnx(_credits_key(user.user_id), user.credits)
        except RedisError:
            AUTH_CACHE_RESULTS_TOTAL.labels(result="populate_error").inc()
    return user


async def invalidate_user_auth_cache(*, api_key: str, redis_client: Redis[str]) -> None:
    """Drop auth cache for an API key after admin updates."""

    await redis_client.delete(_auth_cache_key(api_key))


def credits_cache_key(user_id: UUID) -> str:
    return _credits_key(user_id)


def active_tasks_key(user_id: UUID) -> str:
    return f"active:{user_id}"


def idempotency_key(user_id: UUID, value: str) -> str:
    return f"idem:{user_id}:{value}"


def pending_marker_key(task_id: UUID) -> str:
    return f"pending:{task_id}"


def result_cache_key(task_id: UUID) -> str:
    return f"result:{task_id}"


def parse_bearer_token(raw_authorization: str | None) -> str | None:
    """Parse `Authorization: Bearer <token>` and return token or None."""

    if raw_authorization is None:
        return None

    parts = raw_authorization.strip().split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None

    token_value = token.strip()
    return token_value if token_value else None
