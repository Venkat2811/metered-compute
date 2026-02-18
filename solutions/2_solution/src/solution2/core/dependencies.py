from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
from redis.asyncio import Redis

from solution2.core.settings import AppSettings
from solution2.services.rabbitmq import RabbitMQRelay

AsyncDependencyCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class ReadinessResult:
    """Readiness summary returned from `/ready`."""

    ready: bool
    dependencies: dict[str, bool]


@dataclass(frozen=True)
class DependencyHealthService:
    """Runs readiness checks against external dependencies."""

    check_postgres: AsyncDependencyCheck
    check_redis: AsyncDependencyCheck
    check_rabbitmq: AsyncDependencyCheck | None = None

    async def readiness(self) -> ReadinessResult:
        postgres_ok, redis_ok = await asyncio.gather(
            self._safe_check(self.check_postgres),
            self._safe_check(self.check_redis),
        )
        rabbitmq_ok = (
            await self._safe_check(self.check_rabbitmq) if self.check_rabbitmq is not None else True
        )

        dependencies = {
            "postgres": postgres_ok,
            "redis": redis_ok,
            "rabbitmq": rabbitmq_ok,
        }
        return ReadinessResult(ready=all(dependencies.values()), dependencies=dependencies)

    @staticmethod
    async def _safe_check(check_fn: AsyncDependencyCheck) -> bool:
        try:
            return await check_fn()
        except Exception:
            return False


async def check_postgres(dsn: str, timeout_seconds: float = 1.0) -> bool:
    """Returns True when Postgres is reachable and responds to a probe query."""

    connection = await asyncpg.connect(dsn=dsn, timeout=timeout_seconds)
    try:
        await connection.execute("SELECT 1")
        return True
    finally:
        await connection.close()


async def check_postgres_pool(pool: asyncpg.Pool, timeout_seconds: float = 1.0) -> bool:
    """Returns True when a pooled Postgres probe completes inside timeout budget."""

    async def _probe() -> bool:
        value = await pool.fetchval("SELECT 1")
        return bool(value == 1)

    return bool(await asyncio.wait_for(_probe(), timeout=timeout_seconds))


async def check_redis(url: str, timeout_seconds: float = 1.0) -> bool:
    """Returns True when Redis responds to a ping."""

    client = Redis.from_url(
        url,
        socket_timeout=timeout_seconds,
        socket_connect_timeout=timeout_seconds,
    )
    try:
        pong = await client.ping()
        return bool(pong)
    finally:
        await client.close()


async def check_redis_client(redis_client: Redis[str], timeout_seconds: float = 1.0) -> bool:
    """Returns True when a shared Redis client probe completes inside timeout budget."""

    pong = await asyncio.wait_for(redis_client.ping(), timeout=timeout_seconds)
    return bool(pong)


async def check_rabbitmq(url: str, timeout_seconds: float = 1.0) -> bool:
    """Returns True when RabbitMQ accepts a short-lived probe connection."""

    relay = RabbitMQRelay(
        rabbitmq_url=url,
        socket_connect_timeout=timeout_seconds,
        heartbeat=max(1, int(timeout_seconds)),
    )

    def _probe() -> bool:
        relay.connect()
        relay.close()
        return True

    return bool(await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout_seconds + 0.1))


def build_dependency_health_service(
    settings: AppSettings,
    *,
    db_pool: asyncpg.Pool | None = None,
    redis_client: Redis[str] | None = None,
) -> DependencyHealthService:
    """Create the default dependency checker service for the running app."""

    async def postgres_checker() -> bool:
        if db_pool is not None:
            return await check_postgres_pool(
                db_pool,
                timeout_seconds=settings.readiness_postgres_timeout_seconds,
            )
        return await check_postgres(
            str(settings.postgres_dsn),
            timeout_seconds=settings.readiness_postgres_timeout_seconds,
        )

    async def redis_checker() -> bool:
        if redis_client is not None:
            return await check_redis_client(
                redis_client,
                timeout_seconds=settings.readiness_redis_timeout_seconds,
            )
        return await check_redis(
            str(settings.redis_url),
            timeout_seconds=settings.readiness_redis_timeout_seconds,
        )

    async def rabbitmq_checker() -> bool:
        return await check_rabbitmq(
            settings.rabbitmq_url,
            timeout_seconds=settings.readiness_rabbitmq_timeout_seconds,
        )

    return DependencyHealthService(
        check_postgres=postgres_checker,
        check_redis=redis_checker,
        check_rabbitmq=rabbitmq_checker,
    )
