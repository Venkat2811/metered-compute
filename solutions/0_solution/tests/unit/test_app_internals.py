from __future__ import annotations

import importlib
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from solution0.constants import TaskStatus, UserRole
from solution0.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_AUTH_CACHE_TTL_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PENDING_MARKER_TTL_SECONDS,
    DEFAULT_TASK_COST,
    DEFAULT_TASK_RESULT_TTL_SECONDS,
    DEFAULT_USER1_API_KEY,
)
from solution0.models.domain import AuthUser, TaskRecord
from tests.constants import (
    ALT_USER_ID,
    TEST_USER_ID,
    TEST_USER_NAME,
    V1_ADMIN_CREDITS_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)

os.environ.setdefault("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")


def _request_for(app: FastAPI) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "app": app,
    }
    return Request(scope)


def _load_app_module() -> Any:
    module = cast(Any, importlib.import_module("solution0.app"))
    return cast(Any, importlib.reload(module))


@pytest.mark.asyncio
async def test_runtime_and_health_helpers_raise_without_state() -> None:
    app_module = _load_app_module()
    app = FastAPI()
    request = _request_for(app)

    with pytest.raises(RuntimeError):
        app_module._runtime_state(request)

    with pytest.raises(RuntimeError):
        app_module._health_service(request)


@pytest.mark.asyncio
async def test_check_worker_connectivity_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = _load_app_module()

    class _GoodInspect:
        @staticmethod
        def ping() -> dict[str, str]:
            return {"worker@host": "pong"}

    class _GoodControl:
        @staticmethod
        def inspect(*_: object, **__: object) -> _GoodInspect:
            return _GoodInspect()

    class _BadControl:
        @staticmethod
        def inspect(*_: object, **__: object) -> object:
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(app_module.celery_app, "control", _GoodControl())
    assert await app_module._check_worker_connectivity() is True

    monkeypatch.setattr(app_module.celery_app, "control", _BadControl())
    assert await app_module._check_worker_connectivity() is False


@pytest.mark.asyncio
async def test_lifespan_initializes_runtime_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    app = FastAPI()

    settings = SimpleNamespace(
        app_name="test-api",
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
        redis_url="redis://localhost:6379/0",
        db_statement_timeout_ms=50,
        db_idle_in_transaction_timeout_ms=500,
        db_pool_min_size=1,
        db_pool_max_size=2,
        db_pool_command_timeout_seconds=1.0,
        db_pool_max_inactive_connection_lifetime_seconds=1.0,
        redis_socket_timeout_seconds=0.05,
        redis_socket_connect_timeout_seconds=0.05,
    )

    class _FakePool:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class _FakeRedis:
        def __init__(self) -> None:
            self.closed = False

        async def ping(self) -> bool:
            return True

        async def script_load(self, _: str) -> str:
            return "sha"

        async def close(self) -> None:
            self.closed = True

    fake_pool = _FakePool()
    fake_redis = _FakeRedis()

    async def fake_run_migrations(*_: object, **__: object) -> list[str]:
        return []

    async def fake_create_pool(*_: object, **__: object) -> _FakePool:
        return fake_pool

    monkeypatch.setattr(app_module, "load_settings", lambda: settings)
    monkeypatch.setattr(app_module, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(app_module.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(app_module.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    monkeypatch.setattr(
        app_module,
        "build_dependency_health_service",
        lambda *_args, **_kwargs: SimpleNamespace(readiness=lambda: None),
    )

    async with app_module._lifespan(app):
        assert app.state.runtime.admission_script_sha == "sha"
        assert app.state.runtime.decrement_script_sha == "sha"
        assert app.state.runtime.db_pool is fake_pool
        assert app.state.runtime.redis_client is fake_redis

    assert fake_pool.closed is True
    assert fake_redis.closed is True


def test_create_app_submission_failure_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = _load_app_module()
    app = app_module.create_app()

    user = AuthUser(
        api_key=DEFAULT_USER1_API_KEY,
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )

    async def fake_resolve_user(**_: object) -> AuthUser:
        return user

    async def fake_ready() -> object:
        return SimpleNamespace(
            ready=True, dependencies={"postgres": True, "redis": True, "celery": True}
        )

    class _FakeDependency:
        async def readiness(self) -> object:
            return await fake_ready()

    class _FakeTx:
        async def __aenter__(self) -> _FakeTx:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    class _FakeConn:
        def transaction(self) -> _FakeTx:
            return _FakeTx()

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *_: object) -> None:
            return None

    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            task_cost=DEFAULT_TASK_COST,
            max_concurrent=DEFAULT_MAX_CONCURRENT,
            idempotency_ttl_seconds=DEFAULT_IDEMPOTENCY_TTL_SECONDS,
            pending_marker_ttl_seconds=DEFAULT_PENDING_MARKER_TTL_SECONDS,
            task_result_ttl_seconds=DEFAULT_TASK_RESULT_TTL_SECONDS,
            auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
            readiness_celery_timeout_seconds=1.0,
            admin_api_key=DEFAULT_ADMIN_API_KEY,
            celery_queue_name="celery",
        ),
        db_pool=SimpleNamespace(acquire=lambda: _FakeAcquire()),
        redis_client=SimpleNamespace(
            hset=lambda *_args, **_kwargs: None,
            expire=lambda *_args, **_kwargs: None,
            delete=lambda *_args, **_kwargs: None,
            script_exists=lambda *_args, **_kwargs: [1, 1],
            incr=lambda *_args, **_kwargs: 1,
            hgetall=lambda *_args, **_kwargs: {},
            llen=lambda *_args, **_kwargs: 0,
            set=lambda *_args, **_kwargs: None,
            sadd=lambda *_args, **_kwargs: None,
        ),
        admission_script_sha="sha",
        decrement_script_sha="sha",
    )
    app.state.runtime = runtime
    app.state.dependency_health = _FakeDependency()

    @asynccontextmanager
    async def noop_lifespan(_: Any) -> Any:
        yield

    app.router.lifespan_context = noop_lifespan

    monkeypatch.setattr(app_module, "resolve_user_from_api_key", fake_resolve_user)

    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        no_auth = client.post(V1_TASK_SUBMIT_PATH, json={"x": 1, "y": 1})
        assert no_auth.status_code == 401

        async def gate_exception(**_: object) -> tuple[object, str]:
            raise RuntimeError("gate down")

        monkeypatch.setattr(app_module, "run_admission_gate", gate_exception)
        down = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {DEFAULT_USER1_API_KEY}"},
            json={"x": 1, "y": 1},
        )
        assert down.status_code == 503

        async def cache_miss(**_: object) -> tuple[object, str]:
            return SimpleNamespace(ok=False, reason="CACHE_MISS", existing_task_id=None), "sha"

        monkeypatch.setattr(app_module, "run_admission_gate", cache_miss)
        monkeypatch.setattr(app_module, "hydrate_credits_from_db", lambda **_: True)

        async def retry_exception(**_: object) -> tuple[object, str]:
            raise RuntimeError("still down")

        monkeypatch.setattr(app_module, "run_admission_gate", retry_exception)
        retry_down = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {DEFAULT_USER1_API_KEY}"},
            json={"x": 2, "y": 2},
        )
        assert retry_down.status_code == 503


def test_create_app_poll_and_admin_additional_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = _load_app_module()
    app = app_module.create_app()

    user_id = TEST_USER_ID
    user = AuthUser(
        api_key=DEFAULT_USER1_API_KEY,
        user_id=user_id,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )

    async def fake_resolve_user(**_: object) -> AuthUser:
        return user

    class _FakeDependency:
        async def readiness(self) -> object:
            return SimpleNamespace(
                ready=True, dependencies={"postgres": True, "redis": True, "celery": True}
            )

    class _FakeTx:
        async def __aenter__(self) -> _FakeTx:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    class _FakeConn:
        def transaction(self) -> _FakeTx:
            return _FakeTx()

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *_: object) -> None:
            return None

    async def fake_hgetall(_: str) -> dict[str, str]:
        return {}

    async def fake_llen(_: str) -> int:
        return 3

    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            task_cost=DEFAULT_TASK_COST,
            max_concurrent=DEFAULT_MAX_CONCURRENT,
            idempotency_ttl_seconds=DEFAULT_IDEMPOTENCY_TTL_SECONDS,
            pending_marker_ttl_seconds=DEFAULT_PENDING_MARKER_TTL_SECONDS,
            task_result_ttl_seconds=5,
            auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
            readiness_celery_timeout_seconds=1.0,
            admin_api_key=DEFAULT_ADMIN_API_KEY,
            celery_queue_name="celery",
        ),
        db_pool=SimpleNamespace(acquire=lambda: _FakeAcquire()),
        redis_client=SimpleNamespace(
            hgetall=fake_hgetall,
            llen=fake_llen,
            script_exists=lambda *_args, **_kwargs: [1, 1],
            set=lambda *_args, **_kwargs: None,
            sadd=lambda *_args, **_kwargs: None,
            delete=lambda *_args, **_kwargs: None,
            incr=lambda *_args, **_kwargs: 1,
        ),
        admission_script_sha="sha",
        decrement_script_sha="sha",
    )
    app.state.runtime = runtime
    app.state.dependency_health = _FakeDependency()

    @asynccontextmanager
    async def noop_lifespan(_: Any) -> Any:
        yield

    app.router.lifespan_context = noop_lifespan
    monkeypatch.setattr(app_module, "resolve_user_from_api_key", fake_resolve_user)

    pending_task = TaskRecord(
        task_id=uuid4(),
        api_key=user.api_key,
        user_id=user.user_id,
        x=1,
        y=2,
        cost=DEFAULT_TASK_COST,
        status=TaskStatus.PENDING,
        result=None,
        error=None,
        runtime_ms=None,
        idempotency_key=None,
        created_at=datetime.now(tz=UTC),
        started_at=None,
        completed_at=None,
    )

    async def fake_get_task(_: object, __: UUID) -> TaskRecord:
        return pending_task

    monkeypatch.setattr(app_module, "get_task", fake_get_task)

    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        polled = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": str(pending_task.task_id)},
            headers={"Authorization": f"Bearer {user.api_key}"},
        )
        assert polled.status_code == 200
        assert polled.json()["queue_position"] == 3

        other_task = replace(pending_task, user_id=ALT_USER_ID)

        async def fake_get_other(_: object, __: UUID) -> TaskRecord:
            return other_task

        monkeypatch.setattr(app_module, "get_task", fake_get_other)
        hidden = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": str(pending_task.task_id)},
            headers={"Authorization": f"Bearer {user.api_key}"},
        )
        assert hidden.status_code == 404

        async def fake_get_completed(_: object, __: UUID) -> TaskRecord:
            return replace(
                pending_task,
                status=TaskStatus.COMPLETED,
                completed_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
            )

        monkeypatch.setattr(app_module, "get_task", fake_get_completed)

        async def fake_update_task_expired(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(app_module, "update_task_expired", fake_update_task_expired)
        expired = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": str(pending_task.task_id)},
            headers={"Authorization": f"Bearer {user.api_key}"},
        )
        assert expired.status_code == 200

        async def fake_admin_none(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(app_module, "admin_update_user_credits", fake_admin_none)
        forbidden_admin = client.post(
            V1_ADMIN_CREDITS_PATH,
            headers={"Authorization": f"Bearer {user.api_key}"},
            json={"api_key": user.api_key, "delta": 10, "reason": "topup"},
        )
        assert forbidden_admin.status_code == 403
