from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from solution0.constants import UserRole
from solution0.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_AUTH_CACHE_TTL_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PENDING_MARKER_TTL_SECONDS,
    DEFAULT_TASK_COST,
    DEFAULT_TASK_RESULT_TTL_SECONDS,
)
from solution0.core.dependencies import DependencyHealthService
from solution0.core.runtime import RuntimeState
from solution0.models.domain import AuthUser
from solution0.services.billing import AdmissionDecision
from tests.constants import TEST_USER_ID, TEST_USER_NAME, V1_TASK_SUBMIT_PATH


class FakeCeleryApp:
    @staticmethod
    def send_task(**_: object) -> None:
        raise RuntimeError("broker publish failure")


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

    async def execute(self) -> None:
        for op_name, arg1, arg2 in self._ops:
            if op_name == "hset":
                await self._redis.hset(cast(str, arg1), cast(dict[str, str], arg2))
            elif op_name == "expire":
                await self._redis.expire(cast(str, arg1), cast(int, arg2))


@dataclass
class FakeRedisClient:
    _hashes: dict[str, dict[str, str]]

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self._hashes[key] = dict(mapping)
        return 1

    async def expire(self, key: str, _: int) -> bool:
        return key in self._hashes

    async def delete(self, key: str) -> int:
        existed = key in self._hashes
        self._hashes.pop(key, None)
        return 1 if existed else 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return self._hashes.get(key, {})

    async def llen(self, _: str) -> int:
        return 0

    async def set(self, key: str, value: int) -> bool:
        self._hashes[key] = {"value": str(value)}
        return True

    async def sadd(self, key: str, value: str) -> int:
        existing = self._hashes.get(key, {})
        existing[value] = "1"
        self._hashes[key] = existing
        return 1

    def pipeline(self, *, transaction: bool = False) -> FakeRedisPipeline:
        _ = transaction
        return FakeRedisPipeline(self)


@pytest.mark.fault
def test_submit_returns_503_on_broker_publish_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

    app_module = cast(Any, importlib.import_module("solution0.app"))
    app_module = cast(Any, importlib.reload(app_module))

    fake_redis = FakeRedisClient(_hashes={})
    fake_settings = SimpleNamespace(
        task_cost=DEFAULT_TASK_COST,
        max_concurrent=DEFAULT_MAX_CONCURRENT,
        idempotency_ttl_seconds=DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        pending_marker_ttl_seconds=DEFAULT_PENDING_MARKER_TTL_SECONDS,
        task_result_ttl_seconds=DEFAULT_TASK_RESULT_TTL_SECONDS,
        auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
        readiness_celery_timeout_seconds=1.0,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
        celery_queue_name="celery",
    )
    runtime = RuntimeState(
        settings=fake_settings,  # type: ignore[arg-type]
        db_pool=cast(Any, FakePool()),
        redis_client=cast(Any, fake_redis),
        admission_script_sha="admit-sha",
        decrement_script_sha="decr-sha",
    )

    auth_user = AuthUser(
        api_key="user-key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )

    async def fake_resolve_user(**_: object) -> AuthUser:
        return auth_user

    async def fake_run_admission_gate(**_: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "admit-sha-2"

    async def fake_create_task_record(*_: object, **__: object) -> None:
        return None

    credit_events: list[tuple[int, str]] = []

    async def fake_insert_credit_transaction(*_: object, **kwargs: object) -> None:
        credit_events.append((cast(int, kwargs["delta"]), str(kwargs["reason"])))

    async def fake_update_task_failed(*_: object, **__: object) -> bool:
        return True

    refund_calls = {"count": 0}

    async def fake_refund_and_decrement_active(**_: object) -> str:
        refund_calls["count"] += 1
        return "decr-sha-2"

    async def _ok() -> bool:
        return True

    dependency_health = DependencyHealthService(
        check_postgres=_ok,
        check_redis=_ok,
        check_celery=_ok,
    )

    app = app_module.create_app()
    app.state.runtime = runtime
    app.state.dependency_health = dependency_health

    @asynccontextmanager
    async def _noop_lifespan(_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop_lifespan

    monkeypatch.setattr(app_module, "resolve_user_from_api_key", fake_resolve_user)
    monkeypatch.setattr(app_module, "run_admission_gate", fake_run_admission_gate)
    monkeypatch.setattr(app_module, "create_task_record", fake_create_task_record)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(app_module, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", fake_refund_and_decrement_active)
    monkeypatch.setattr(app_module, "celery_app", FakeCeleryApp())

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={
                "Authorization": "Bearer user-key",
                "Idempotency-Key": str(uuid4()),
            },
            json={"x": 5, "y": 8},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_DEGRADED"
    assert refund_calls["count"] == 1
    assert (-10, "task_deduct") in credit_events
    assert (10, "publish_refund") in credit_events
