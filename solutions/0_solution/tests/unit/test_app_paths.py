from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from solution0.constants import TaskStatus, UserRole
from solution0.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_AUTH_CACHE_TTL_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PENDING_MARKER_TTL_SECONDS,
    DEFAULT_TASK_COST,
    DEFAULT_TASK_RESULT_TTL_SECONDS,
)
from solution0.core.dependencies import DependencyHealthService
from solution0.core.runtime import RuntimeState
from solution0.models.domain import AuthUser, TaskRecord
from solution0.services.billing import AdmissionDecision
from tests.constants import (
    ADMIN_USER_ID,
    ALT_USER_ID,
    TASK_ID_PRIMARY,
    TASK_ID_SECONDARY,
    TASK_ID_TERTIARY,
    TEST_ADMIN_NAME,
    TEST_USER_ID,
    TEST_USER_NAME,
    V1_ADMIN_CREDITS_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)


class _FakeTxContext:
    async def __aenter__(self) -> _FakeTxContext:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConnection:
    def transaction(self) -> _FakeTxContext:
        return _FakeTxContext()


class _FakeAcquireContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self._connection = _FakeConnection()

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self._connection)


class _FakeRedisPipeline:
    def __init__(self, redis_client: _FakeRedisClient) -> None:
        self._redis = redis_client
        self._ops: list[tuple[str, object, object]] = []

    async def __aenter__(self) -> _FakeRedisPipeline:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def hset(self, key: str, mapping: dict[str, str]) -> _FakeRedisPipeline:
        self._ops.append(("hset", key, dict(mapping)))
        return self

    def expire(self, key: str, ttl_seconds: int) -> _FakeRedisPipeline:
        self._ops.append(("expire", key, ttl_seconds))
        return self

    async def execute(self) -> None:
        for op_name, arg1, arg2 in self._ops:
            if op_name == "hset":
                await self._redis.hset(cast(str, arg1), cast(dict[str, str], arg2))
            elif op_name == "expire":
                await self._redis.expire(cast(str, arg1), cast(int, arg2))


@dataclass
class _FakeRedisClient:
    hashes: dict[str, dict[str, str]]
    values: dict[str, int]
    sets: dict[str, set[str]]
    hset_calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    queue_depth: int = 0
    script_exists_values: list[int] | None = None
    fail_hgetall: bool = False
    fail_queue_depth: bool = False
    fail_script_exists: bool = False
    fail_cache_sync: bool = False

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

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def pipeline(self, *, transaction: bool = False) -> _FakeRedisPipeline:
        _ = transaction
        return _FakeRedisPipeline(self)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
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


def _task(task_id: UUID, user_id: UUID, *, x: int, y: int, status: TaskStatus) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        api_key="key",
        user_id=user_id,
        x=x,
        y=y,
        cost=10,
        status=status,
        result={"z": x + y} if status == TaskStatus.COMPLETED else None,
        error=None,
        runtime_ms=100,
        idempotency_key="idem",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        completed_at=datetime.now(tz=UTC)
        if status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        else None,
    )


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    redis_client: _FakeRedisClient,
    auth_user: AuthUser,
    worker_ready: bool = True,
    dependency_ready: bool = True,
    db_pool: Any | None = None,
) -> tuple[Any, TestClient]:
    app_module = cast(Any, importlib.import_module("solution0.app"))
    app_module = cast(Any, importlib.reload(app_module))

    runtime = RuntimeState(
        settings=_settings(),  # type: ignore[arg-type]
        db_pool=cast(Any, db_pool if db_pool is not None else _FakePool()),
        redis_client=cast(Any, redis_client),
        admission_script_sha="admit-sha",
        decrement_script_sha="decr-sha",
    )

    async def _dep_status() -> bool:
        return dependency_ready

    dependency_health = DependencyHealthService(
        check_postgres=_dep_status,
        check_redis=_dep_status,
        check_celery=_dep_status,
    )

    app = app_module.create_app()
    app.state.runtime = runtime
    app.state.dependency_health = dependency_health

    @asynccontextmanager
    async def _noop_lifespan(_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop_lifespan

    async def fake_resolve_user(**_: object) -> AuthUser:
        return auth_user

    async def fake_get_task_default(*_: object, **__: object) -> None:
        return None

    async def fake_worker_check(*_: object, **__: object) -> bool:
        return worker_ready

    monkeypatch.setattr(app_module, "resolve_user_from_api_key", fake_resolve_user)
    monkeypatch.setattr(app_module, "get_task", fake_get_task_default)
    monkeypatch.setattr(app_module, "_check_worker_connectivity", fake_worker_check)

    return app_module, TestClient(app, raise_server_exceptions=False)


def test_ready_requires_dependency_worker_and_script_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={}, script_exists_values=[1, 1])
    _, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["dependencies"]["worker"] is True
    assert ready.json()["dependencies"]["redis_scripts"] is True
    client.close()

    redis_client.script_exists_values = [1, 0]
    _, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)
    degraded = client.get("/ready")
    assert degraded.status_code == 503
    client.close()

    _, client = _build_app(
        monkeypatch, redis_client=redis_client, auth_user=user, worker_ready=False
    )
    degraded_worker = client.get("/ready")
    assert degraded_worker.status_code == 503
    client.close()


def test_submit_reject_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)

    async def reject_concurrency(**_: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None), "sha"

    monkeypatch.setattr(app_module, "run_admission_gate", reject_concurrency)
    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key"},
        json={"x": 1, "y": 2},
    )
    assert response.status_code == 429

    async def reject_insufficient(**_: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=False, reason="INSUFFICIENT", existing_task_id=None), "sha"

    monkeypatch.setattr(app_module, "run_admission_gate", reject_insufficient)
    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key"},
        json={"x": 1, "y": 2},
    )
    assert response.status_code == 402

    async def cache_miss(**_: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=False, reason="CACHE_MISS", existing_task_id=None), "sha"

    async def hydrate_false(**_: object) -> bool:
        return False

    monkeypatch.setattr(app_module, "run_admission_gate", cache_miss)
    monkeypatch.setattr(app_module, "hydrate_credits_from_db", hydrate_false)
    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key"},
        json={"x": 1, "y": 2},
    )
    assert response.status_code == 401

    async def hydrate_error(**_: object) -> bool:
        raise RuntimeError("hydrate failed")

    monkeypatch.setattr(app_module, "hydrate_credits_from_db", hydrate_error)
    degraded = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key"},
        json={"x": 1, "y": 2},
    )
    assert degraded.status_code == 503
    client.close()


def test_submit_idempotent_conflict_and_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)

    existing_task_id = TASK_ID_PRIMARY

    async def idempotent_decision(**_: object) -> tuple[AdmissionDecision, str]:
        return (
            AdmissionDecision(
                ok=False,
                reason="IDEMPOTENT",
                existing_task_id=str(existing_task_id),
            ),
            "sha",
        )

    async def fake_get_task(*_: object, **__: object) -> TaskRecord:
        return _task(existing_task_id, user.user_id, x=1, y=2, status=TaskStatus.COMPLETED)

    monkeypatch.setattr(app_module, "run_admission_gate", idempotent_decision)
    monkeypatch.setattr(app_module, "get_task", fake_get_task)

    conflict = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key", "Idempotency-Key": "idem-1"},
        json={"x": 9, "y": 2},
    )
    assert conflict.status_code == 409

    replay = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key", "Idempotency-Key": "idem-1"},
        json={"x": 1, "y": 2},
    )
    assert replay.status_code == 200
    assert replay.json()["task_id"] == str(existing_task_id)
    client.close()


def test_submit_accept_path_and_hit_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)

    async def accept(**_: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "sha2"

    persisted: list[str] = []

    async def fake_create_task_record(*_: object, **__: object) -> None:
        persisted.append("task")

    async def fake_insert_credit_transaction(*_: object, **kwargs: object) -> None:
        persisted.append(str(kwargs["reason"]))

    celery_calls: dict[str, Any] = {}

    class _FakeCelery:
        @staticmethod
        def send_task(*args: object, **kwargs: object) -> None:
            celery_calls["args"] = args
            celery_calls["kwargs"] = kwargs
            return None

    monkeypatch.setattr(app_module, "run_admission_gate", accept)
    monkeypatch.setattr(app_module, "create_task_record", fake_create_task_record)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(app_module, "celery_app", _FakeCelery())

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": "Bearer key",
            "Idempotency-Key": f"idem-{uuid4()}",
            "X-Trace-Id": "trace-test-123",
        },
        json={"x": 3, "y": 4},
    )
    assert response.status_code == 201
    task_id = UUID(str(response.json()["task_id"]))
    assert task_id.version == 7
    assert "task_deduct" in persisted
    assert celery_calls["kwargs"]["args"][-1] == "trace-test-123"
    pending_writes = [
        mapping for key, mapping in redis_client.hset_calls if key.startswith("pending:")
    ]
    assert pending_writes
    assert "api_key" not in pending_writes[0]

    hit = client.get("/hit")
    assert hit.status_code == 200
    assert "Hello World!" in hit.json()["message"]
    client.close()


def test_submit_rejects_out_of_range_integers(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    _, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key"},
        json={"x": 2**40, "y": 2},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    client.close()


def test_poll_cancel_and_admin_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)

    missing = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(uuid4())},
        headers={"Authorization": "Bearer key"},
    )
    assert missing.status_code == 404

    redis_client.fail_hgetall = True
    degraded = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(uuid4())},
        headers={"Authorization": "Bearer key"},
    )
    assert degraded.status_code == 503
    redis_client.fail_hgetall = False

    cached_id = str(uuid4())
    redis_client.hashes[f"result:{cached_id}"] = {
        "task_id": cached_id,
        "user_id": str(user.user_id),
        "status": "COMPLETED",
        "result": json.dumps({"z": 7}),
        "error": "",
        "queue_position": "",
        "estimated_seconds": "",
        "expires_at": datetime.now(tz=UTC).isoformat(),
    }
    cached = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": cached_id},
        headers={"Authorization": "Bearer key"},
    )
    assert cached.status_code == 200
    assert cached.json()["result"] == {"z": 7}

    redis_client.hashes[f"result:{cached_id}"]["user_id"] = str(ALT_USER_ID)
    hidden_cached = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": cached_id},
        headers={"Authorization": "Bearer key"},
    )
    assert hidden_cached.status_code == 404

    target_id = TASK_ID_SECONDARY

    async def fake_get_task(*_: object, **__: object) -> TaskRecord:
        return _task(target_id, user.user_id, x=2, y=5, status=TaskStatus.PENDING)

    async def fake_update_task_cancelled(*_: object, **__: object) -> bool:
        return True

    async def fake_insert_credit_transaction(*_: object, **__: object) -> None:
        return None

    async def fake_refund_and_decrement_active(**_: object) -> str:
        return "sha3"

    class _FakeControl:
        @staticmethod
        def revoke(*_: object, **__: object) -> None:
            return None

    monkeypatch.setattr(app_module, "get_task", fake_get_task)
    monkeypatch.setattr(app_module, "update_task_cancelled", fake_update_task_cancelled)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", fake_refund_and_decrement_active)
    monkeypatch.setattr(app_module.celery_app, "control", _FakeControl())

    cancelled = client.post(
        f"/v1/task/{target_id}/cancel",
        headers={"Authorization": "Bearer key"},
    )
    assert cancelled.status_code == 200

    forbidden = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": "Bearer key"},
        json={"api_key": DEFAULT_ALICE_API_KEY, "delta": 1, "reason": "test"},
    )
    assert forbidden.status_code == 403
    client.close()

    admin = AuthUser(
        api_key=DEFAULT_ADMIN_API_KEY,
        user_id=ADMIN_USER_ID,
        name=TEST_ADMIN_NAME,
        role=UserRole.ADMIN,
        credits=1000,
    )
    redis_client.fail_cache_sync = True
    app_module, admin_client = _build_app(monkeypatch, redis_client=redis_client, auth_user=admin)

    async def fake_admin_update_user_credits(*_: object, **__: object) -> tuple[UUID, int]:
        return TEST_USER_ID, 999

    monkeypatch.setattr(app_module, "admin_update_user_credits", fake_admin_update_user_credits)

    warning_events: list[dict[str, str]] = []

    def fake_warning(event: str, **kwargs: object) -> None:
        if event == "admin_credit_cache_sync_failed":
            warning_events.append({str(key): str(value) for key, value in kwargs.items()})

    monkeypatch.setattr(app_module.logger, "warning", fake_warning)

    updated = admin_client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {DEFAULT_ADMIN_API_KEY}"},
        json={
            "api_key": DEFAULT_ALICE_API_KEY,
            "delta": 10,
            "reason": "manual_topup",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["new_balance"] == 999
    assert warning_events
    warning_payload = warning_events[0]
    assert warning_payload["target_api_key_masked"].startswith(DEFAULT_ALICE_API_KEY[:4])
    assert warning_payload["target_api_key_masked"].endswith(DEFAULT_ALICE_API_KEY[-4:])
    assert "api_key" not in warning_payload

    async def fake_admin_update_user_credits_none(*_: object, **__: object) -> None:
        return None

    async def fake_admin_update_user_credits_error(*_: object, **__: object) -> tuple[UUID, int]:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        app_module, "admin_update_user_credits", fake_admin_update_user_credits_none
    )
    missing_user = admin_client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {DEFAULT_ADMIN_API_KEY}"},
        json={"api_key": DEFAULT_ALICE_API_KEY, "delta": 10, "reason": "manual_topup"},
    )
    assert missing_user.status_code == 404

    monkeypatch.setattr(
        app_module, "admin_update_user_credits", fake_admin_update_user_credits_error
    )
    degraded_admin = admin_client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {DEFAULT_ADMIN_API_KEY}"},
        json={"api_key": DEFAULT_ALICE_API_KEY, "delta": 10, "reason": "manual_topup"},
    )
    assert degraded_admin.status_code == 503
    admin_client.close()


def test_cancel_conflict_when_state_changes_during_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(monkeypatch, redis_client=redis_client, auth_user=user)

    target_id = TASK_ID_TERTIARY

    async def fake_get_task(*_: object, **__: object) -> TaskRecord:
        return _task(target_id, user.user_id, x=1, y=2, status=TaskStatus.RUNNING)

    async def fake_update_task_cancelled(*_: object, **__: object) -> bool:
        return False

    async def fake_insert_credit_transaction(*_: object, **__: object) -> None:
        raise AssertionError("credit transaction should not be inserted when cancel is not applied")

    async def fake_refund_and_decrement_active(**_: object) -> str:
        raise AssertionError("refund should not be executed when cancel is not applied")

    class _FakeControl:
        @staticmethod
        def revoke(*_: object, **__: object) -> None:
            return None

    monkeypatch.setattr(app_module, "get_task", fake_get_task)
    monkeypatch.setattr(app_module, "update_task_cancelled", fake_update_task_cancelled)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", fake_refund_and_decrement_active)
    monkeypatch.setattr(app_module.celery_app, "control", _FakeControl())

    response = client.post(
        f"/v1/task/{target_id}/cancel",
        headers={"Authorization": "Bearer key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    client.close()


def test_submit_returns_503_on_pool_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})

    class _ExhaustedPool:
        def acquire(self) -> object:
            raise TimeoutError("pool exhausted")

    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        db_pool=_ExhaustedPool(),
    )

    async def accept(**_: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "sha"

    async def fake_refund_and_decrement_active(**_: object) -> str:
        return "sha-updated"

    monkeypatch.setattr(app_module, "run_admission_gate", accept)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", fake_refund_and_decrement_active)

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key", "Idempotency-Key": f"idem-{uuid4()}"},
        json={"x": 6, "y": 7},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_DEGRADED"
    client.close()
