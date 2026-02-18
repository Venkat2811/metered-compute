from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from solution2.constants import UserRole
from solution2.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_AUTH_CACHE_TTL_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PENDING_MARKER_TTL_SECONDS,
    DEFAULT_TASK_COST,
    DEFAULT_TASK_RESULT_TTL_SECONDS,
)
from solution2.core.dependencies import DependencyHealthService
from solution2.core.runtime import RuntimeState
from solution2.models.domain import AuthUser
from solution2.services.auth import task_state_key
from solution2.services.billing import AdmissionDecision
from tests.constants import TEST_USER_ID, TEST_USER_NAME, V1_TASK_SUBMIT_PATH
from tests.fakes import FakePool, FakeRedisClient


@pytest.mark.fault
def test_submit_returns_503_on_task_persist_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    app_module = cast(Any, importlib.import_module("solution2.app"))
    app_module = cast(Any, importlib.reload(app_module))

    fake_redis = FakeRedisClient(hashes={})
    fake_settings = SimpleNamespace(
        task_cost=DEFAULT_TASK_COST,
        max_concurrent=DEFAULT_MAX_CONCURRENT,
        idempotency_ttl_seconds=DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        pending_marker_ttl_seconds=DEFAULT_PENDING_MARKER_TTL_SECONDS,
        reservation_ttl_seconds=300,
        task_result_ttl_seconds=DEFAULT_TASK_RESULT_TTL_SECONDS,
        redis_task_state_ttl_seconds=86400,
        auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
        readiness_worker_timeout_seconds=1.0,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
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
        scopes=frozenset({"task:submit", "task:poll", "task:cancel"}),
    )

    async def fake_resolve_user(*, token: str, request: object) -> AuthUser:
        assert token == "jwt.header.signature"
        _ = request
        return auth_user

    async def fake_run_admission_gate(**_: object) -> tuple[AdmissionDecision, str]:
        task_id = cast(Any, _["task_id"])
        keys["task_state"] = task_state_key(task_id)
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "admit-sha-2"

    async def fake_hset_fail(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("persist failure")

    keys: dict[str, str] = {}

    async def _ok() -> bool:
        return True

    dependency_health = DependencyHealthService(
        check_postgres=_ok,
        check_redis=_ok,
    )

    app = app_module.create_app()
    app.state.runtime = runtime
    app.state.dependency_health = dependency_health

    @asynccontextmanager
    async def _noop_lifespan(_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop_lifespan

    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_user)
    monkeypatch.setattr(app_module, "run_admission_gate", fake_run_admission_gate)
    monkeypatch.setattr(fake_redis, "hset", fake_hset_fail)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={
                "Authorization": "Bearer jwt.header.signature",
                "Idempotency-Key": str(uuid4()),
            },
            json={"x": 5, "y": 8},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_DEGRADED"
    assert keys
    assert keys["task_state"] not in fake_redis._hashes
