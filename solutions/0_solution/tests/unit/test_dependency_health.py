from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import solution0.core.dependencies as dependencies_module
from solution0.core.dependencies import (
    DependencyHealthService,
    build_dependency_health_service,
    check_postgres_pool,
    check_redis_client,
)


@pytest.mark.asyncio
async def test_readiness_is_true_when_all_dependencies_are_healthy() -> None:
    async def postgres_checker() -> bool:
        return True

    async def redis_checker() -> bool:
        return True

    async def celery_checker() -> bool:
        return True

    service = DependencyHealthService(
        check_postgres=postgres_checker,
        check_redis=redis_checker,
        check_celery=celery_checker,
    )

    readiness = await service.readiness()

    assert readiness.ready is True
    assert readiness.dependencies == {
        "postgres": True,
        "redis": True,
        "celery": True,
    }


@pytest.mark.asyncio
async def test_readiness_is_false_when_one_dependency_fails() -> None:
    async def postgres_checker() -> bool:
        return True

    async def redis_checker() -> bool:
        return False

    async def celery_checker() -> bool:
        return True

    service = DependencyHealthService(
        check_postgres=postgres_checker,
        check_redis=redis_checker,
        check_celery=celery_checker,
    )

    readiness = await service.readiness()

    assert readiness.ready is False
    assert readiness.dependencies["redis"] is False


@pytest.mark.asyncio
async def test_check_postgres_pool_uses_shared_pool() -> None:
    class _FakePool:
        def __init__(self) -> None:
            self.calls = 0

        async def fetchval(self, _: str) -> int:
            self.calls += 1
            return 1

    pool = _FakePool()
    assert await check_postgres_pool(cast(Any, pool), timeout_seconds=0.1) is True
    assert pool.calls == 1


@pytest.mark.asyncio
async def test_check_redis_client_uses_shared_client() -> None:
    class _FakeRedis:
        def __init__(self) -> None:
            self.calls = 0

        async def ping(self) -> bool:
            self.calls += 1
            return True

    redis_client = _FakeRedis()
    assert await check_redis_client(cast(Any, redis_client), timeout_seconds=0.1) is True
    assert redis_client.calls == 1


@pytest.mark.asyncio
async def test_build_dependency_health_service_prefers_shared_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, int] = {
        "pg_pool": 0,
        "pg_dsn": 0,
        "redis_client": 0,
        "redis_url": 0,
    }

    async def fake_check_postgres_pool(*_: object, **__: object) -> bool:
        calls["pg_pool"] += 1
        return True

    async def fake_check_postgres(*_: object, **__: object) -> bool:
        calls["pg_dsn"] += 1
        return True

    async def fake_check_redis_client(*_: object, **__: object) -> bool:
        calls["redis_client"] += 1
        return True

    async def fake_check_redis(*_: object, **__: object) -> bool:
        calls["redis_url"] += 1
        return True

    async def fake_check_celery_broker(*_: object, **__: object) -> bool:
        return True

    monkeypatch.setattr(dependencies_module, "check_postgres_pool", fake_check_postgres_pool)
    monkeypatch.setattr(dependencies_module, "check_postgres", fake_check_postgres)
    monkeypatch.setattr(dependencies_module, "check_redis_client", fake_check_redis_client)
    monkeypatch.setattr(dependencies_module, "check_redis", fake_check_redis)
    monkeypatch.setattr(dependencies_module, "check_celery_broker", fake_check_celery_broker)

    settings = SimpleNamespace(
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        readiness_postgres_timeout_seconds=1.0,
        readiness_redis_timeout_seconds=1.0,
        readiness_celery_timeout_seconds=1.0,
    )
    service = build_dependency_health_service(
        cast(Any, settings),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, object()),
    )
    readiness = await service.readiness()
    assert readiness.ready is True
    assert calls["pg_pool"] == 1
    assert calls["pg_dsn"] == 0
    assert calls["redis_client"] == 1
    assert calls["redis_url"] == 0
