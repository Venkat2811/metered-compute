from __future__ import annotations

import time
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import NoScriptError

from solution0.db.repository import fetch_user_credits_by_api_key
from solution0.models.domain import AdmissionDecision
from solution0.observability.metrics import CREDIT_LUA_DURATION_SECONDS
from solution0.services.auth import (
    active_tasks_key,
    credits_cache_key,
    idempotency_key,
)
from solution0.utils.lua_scripts import ADMISSION_LUA, DECR_ACTIVE_CLAMP_LUA, parse_lua_result

__all__ = [
    "AdmissionDecision",
    "decrement_active_counter",
    "hydrate_credits_from_db",
    "mark_credit_dirty",
    "refund_and_decrement_active",
    "run_admission_gate",
]


async def hydrate_credits_from_db(
    *,
    redis_client: Redis[str],
    db_pool: asyncpg.Pool,
    api_key: str,
    user_id: UUID,
) -> bool:
    """Hydrate credits cache from Postgres for admission retry."""

    credits = await fetch_user_credits_by_api_key(db_pool, api_key)
    if credits is None:
        return False

    await redis_client.set(credits_cache_key(user_id), credits)
    return True


async def run_admission_gate(
    *,
    redis_client: Redis[str],
    admission_script_sha: str,
    user_id: UUID,
    task_id: UUID,
    cost: int,
    idempotency_value: str,
    idempotency_ttl_seconds: int,
    max_concurrent: int,
) -> tuple[AdmissionDecision, str]:
    """Execute atomic admission gate and parse typed decision."""

    start = time.perf_counter()
    try:
        raw = await redis_client.evalsha(
            admission_script_sha,
            3,
            credits_cache_key(user_id),
            idempotency_key(user_id, idempotency_value),
            active_tasks_key(user_id),
            str(cost),
            str(task_id),
            str(max_concurrent),
            str(idempotency_ttl_seconds),
        )
    except NoScriptError:
        admission_script_sha = str(await redis_client.script_load(ADMISSION_LUA))
        raw = await redis_client.evalsha(
            admission_script_sha,
            3,
            credits_cache_key(user_id),
            idempotency_key(user_id, idempotency_value),
            active_tasks_key(user_id),
            str(cost),
            str(task_id),
            str(max_concurrent),
            str(idempotency_ttl_seconds),
        )
    duration = time.perf_counter() - start

    payload = str(raw)
    parsed = parse_lua_result(payload)
    CREDIT_LUA_DURATION_SECONDS.labels(result=parsed.reason).observe(duration)
    return (
        AdmissionDecision(ok=parsed.ok, reason=parsed.reason, existing_task_id=parsed.task_id),
        admission_script_sha,
    )


async def mark_credit_dirty(*, redis_client: Redis[str], user_id: UUID) -> None:
    """Add user credit key to dirty set for periodic snapshotting."""

    await redis_client.sadd("credits:dirty", credits_cache_key(user_id))


async def refund_and_decrement_active(
    *,
    redis_client: Redis[str],
    decrement_script_sha: str,
    user_id: UUID,
    amount: int,
) -> str:
    """Compensate by refunding credits and decrementing active counters safely."""

    await redis_client.incrby(credits_cache_key(user_id), amount)
    await mark_credit_dirty(redis_client=redis_client, user_id=user_id)
    return await decrement_active_counter(
        redis_client=redis_client,
        decrement_script_sha=decrement_script_sha,
        user_id=user_id,
    )


async def decrement_active_counter(
    *,
    redis_client: Redis[str],
    decrement_script_sha: str,
    user_id: UUID,
) -> str:
    try:
        await redis_client.evalsha(decrement_script_sha, 1, active_tasks_key(user_id))
    except NoScriptError:
        decrement_script_sha = str(await redis_client.script_load(DECR_ACTIVE_CLAMP_LUA))
        await redis_client.evalsha(decrement_script_sha, 1, active_tasks_key(user_id))
    return decrement_script_sha
