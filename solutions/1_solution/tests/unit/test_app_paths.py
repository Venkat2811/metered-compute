from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from solution1.constants import SubscriptionTier, TaskStatus, UserRole
from solution1.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_AUTH_CACHE_TTL_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PENDING_MARKER_TTL_SECONDS,
    DEFAULT_TASK_COST,
    DEFAULT_TASK_RESULT_TTL_SECONDS,
)
from solution1.core.dependencies import DependencyHealthService
from solution1.core.runtime import RuntimeState
from solution1.models.domain import AuthUser, TaskRecord
from solution1.services.billing import AdmissionDecision
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
    V1_AUTH_REVOKE_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
    V1_WEBHOOK_PATH,
)
from tests.fakes import FakePool as _FakePool
from tests.fakes import FakeRedisClient as _FakeRedisClient


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        task_cost=DEFAULT_TASK_COST,
        max_concurrent=DEFAULT_MAX_CONCURRENT,
        idempotency_ttl_seconds=DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        pending_marker_ttl_seconds=DEFAULT_PENDING_MARKER_TTL_SECONDS,
        task_result_ttl_seconds=DEFAULT_TASK_RESULT_TTL_SECONDS,
        redis_tasks_stream_key="tasks:stream",
        redis_tasks_stream_maxlen=500000,
        redis_task_state_ttl_seconds=86400,
        stream_worker_heartbeat_key="workers:stream:last_seen",
        auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
        readiness_worker_timeout_seconds=1.0,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
        alice_api_key=DEFAULT_ALICE_API_KEY,
        bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        hydra_public_url="http://hydra:4444",
        hydra_issuer="http://hydra:4444/",
        hydra_jwks_url="http://hydra:4444/.well-known/jwks.json",
        hydra_jwks_cache_ttl_seconds=300.0,
        hydra_expected_audience=None,
        oauth_default_scope="task:submit task:poll task:cancel",
        oauth_request_timeout_seconds=3.0,
        oauth_token_rate_limit_enabled=True,
        oauth_token_rate_limit_window_seconds=60,
        oauth_token_rate_limit_max_requests=120,
        revocation_bucket_ttl_seconds=129_600,
        oauth_admin_client_id="solution1-admin",
        oauth_admin_client_secret="solution1-admin-secret",
        oauth_user1_client_id="solution1-user1",
        oauth_user1_client_secret="solution1-user1-secret",
        oauth_user2_client_id="solution1-user2",
        oauth_user2_client_secret="solution1-user2-secret",
        oauth_admin_tier=SubscriptionTier.ENTERPRISE,
        oauth_user1_tier=SubscriptionTier.PRO,
        oauth_user2_tier=SubscriptionTier.FREE,
        oauth_admin_user_id=str(ADMIN_USER_ID),
        oauth_user1_user_id=str(TEST_USER_ID),
        oauth_user2_user_id=str(ALT_USER_ID),
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
    bypass_auth: bool = True,
) -> tuple[Any, TestClient]:
    app_module = cast(Any, importlib.import_module("solution1.app"))
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
    )

    app = app_module.create_app()
    app.state.runtime = runtime
    app.state.dependency_health = dependency_health

    @asynccontextmanager
    async def _noop_lifespan(_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop_lifespan

    async def fake_resolve_user(*, token: str, request: Any) -> AuthUser:
        _ = token
        _ = request
        return auth_user

    async def fake_get_task_default(*_: object, **__: object) -> None:
        return None

    async def fake_worker_check(*_: object, **__: object) -> bool:
        return worker_ready

    def fake_require_scopes(*_: object, **__: object) -> None:
        return None

    if bypass_auth:
        monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_user)
        monkeypatch.setattr(
            app_module,
            "parse_bearer_token",
            lambda raw: "jwt.header.signature" if raw is not None else None,
        )
    monkeypatch.setattr(app_module, "get_task", fake_get_task_default)
    monkeypatch.setattr(app_module, "_check_worker_connectivity", fake_worker_check)
    monkeypatch.setattr(app_module, "_require_scopes", fake_require_scopes)

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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

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


def test_submit_rejects_out_of_range_integers(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    _, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key", "Idempotency-Key": f"idem-{uuid4()}"},
        json={"x": 2**40, "y": 2},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

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
        tier=SubscriptionTier.PRO,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    admission_calls: list[dict[str, object]] = []

    async def accept(**kwargs: object) -> tuple[AdmissionDecision, str]:
        admission_calls.append(dict(kwargs))
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "sha2"

    persisted: list[str] = []

    async def fake_create_task_record(*_: object, **__: object) -> None:
        persisted.append("task")

    async def fake_insert_credit_transaction(*_: object, **kwargs: object) -> None:
        persisted.append(str(kwargs["reason"]))

    monkeypatch.setattr(app_module, "run_admission_gate", accept)
    monkeypatch.setattr(app_module, "create_task_record", fake_create_task_record)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": "Bearer key",
            "Idempotency-Key": f"idem-{uuid4()}",
            "X-Trace-Id": "trace-test-123",
        },
        json={"x": 3, "y": 4, "model_class": "large"},
    )
    assert response.status_code == 201
    task_id = UUID(str(response.json()["task_id"]))
    assert task_id.version == 7
    assert response.json()["estimated_seconds"] == 7
    assert "task_deduct" in persisted
    assert len(admission_calls) == 1
    assert admission_calls[0]["cost"] == DEFAULT_TASK_COST * 5
    assert admission_calls[0]["max_concurrent"] == DEFAULT_MAX_CONCURRENT * 2
    pending_writes = [
        mapping for key, mapping in redis_client.hset_calls if key.startswith("pending:")
    ]
    assert pending_writes
    assert "api_key" not in pending_writes[0]

    hit = client.get("/hit")
    assert hit.status_code == 200
    assert "Hello World!" in hit.json()["message"]
    client.close()


def test_submit_includes_trace_context_in_stream_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
        tier=SubscriptionTier.PRO,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    admission_payloads: list[dict[str, object]] = []

    async def accept(**kwargs: object) -> tuple[AdmissionDecision, str]:
        stream_payload = cast(dict[str, object], kwargs["stream_payload"])
        admission_payloads.append(stream_payload)
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "sha2"

    async def fake_create_task_record(*_: object, **__: object) -> None:
        return None

    async def fake_insert_credit_transaction(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(app_module, "run_admission_gate", accept)
    monkeypatch.setattr(app_module, "create_task_record", fake_create_task_record)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        "solution1.api.task_write_routes.inject_current_trace_context",
        lambda: {"traceparent": "00-abc-def-01"},
    )

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key"},
        json={"x": 3, "y": 4},
    )

    assert response.status_code == 201
    assert admission_payloads
    assert admission_payloads[0]["trace_context"] == {"traceparent": "00-abc-def-01"}
    client.close()


def test_submit_persists_failures_are_compensated(monkeypatch: pytest.MonkeyPatch) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={})
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    async def accept(**kwargs: object) -> tuple[AdmissionDecision, str]:
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None), "sha2"

    async def _create_task_record(*_: object, **__: object) -> None:
        return None

    async def _insert_credit_transaction(*_: object, **__: object) -> None:
        raise RuntimeError("credit ledger unavailable")

    refunds: list[tuple[UUID, int]] = []

    async def _track_refund(**kwargs: object) -> str:
        refunds.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "sha-refund"

    monkeypatch.setattr(app_module, "run_admission_gate", accept)
    monkeypatch.setattr(app_module, "create_task_record", _create_task_record)
    monkeypatch.setattr(app_module, "insert_credit_transaction", _insert_credit_transaction)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", _track_refund)

    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": "Bearer key",
            "Idempotency-Key": "idem-persist-fail",
        },
        json={"x": 3, "y": 4},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_DEGRADED"
    assert refunds == [(TEST_USER_ID, DEFAULT_TASK_COST)]
    assert all(not key.startswith("pending:") for key in redis_client.hashes)
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

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

    monkeypatch.setattr(app_module, "get_task", fake_get_task)
    monkeypatch.setattr(app_module, "update_task_cancelled", fake_update_task_cancelled)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", fake_refund_and_decrement_active)

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


def test_poll_uses_redis_task_state_without_db_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    redis_client = _FakeRedisClient(hashes={}, values={}, sets={}, queue_depth=4)
    redis_client.hashes[f"task:{TASK_ID_PRIMARY}"] = {
        "status": "PENDING",
        "user_id": str(TEST_USER_ID),
        "cost": "10",
        "created_at_epoch": str(int(datetime.now(tz=UTC).timestamp())),
    }
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    async def fake_get_task(*_: object, **__: object) -> TaskRecord | None:
        raise AssertionError("poll should not hit DB when Redis task state exists")

    monkeypatch.setattr(app_module, "get_task", fake_get_task)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer key"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "PENDING"
    assert payload["queue_position"] == 4
    assert payload["estimated_seconds"] == 8
    client.close()


def test_poll_terminal_task_state_falls_back_to_db_record(
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
    redis_client.hashes[f"task:{TASK_ID_PRIMARY}"] = {
        "status": "COMPLETED",
        "user_id": str(TEST_USER_ID),
        "cost": "10",
        "created_at_epoch": str(int(datetime.now(tz=UTC).timestamp())),
    }
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    db_calls = {"count": 0}

    async def fake_get_task(*_: object, **__: object) -> TaskRecord | None:
        db_calls["count"] += 1
        return _task(TASK_ID_PRIMARY, TEST_USER_ID, x=2, y=5, status=TaskStatus.COMPLETED)

    monkeypatch.setattr(app_module, "get_task", fake_get_task)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "COMPLETED"
    assert payload["result"] == {"z": 7}
    assert db_calls["count"] == 1
    client.close()


def test_submit_rejects_oversized_idempotency_key(
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
    _app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    too_long_key = "i" * 129
    response = client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": "Bearer key", "Idempotency-Key": too_long_key},
        json={"x": 1, "y": 1},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert "Idempotency-Key" in response.json()["error"]["message"]
    client.close()


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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    target_id = TASK_ID_TERTIARY

    async def fake_get_task(*_: object, **__: object) -> TaskRecord:
        return _task(target_id, user.user_id, x=1, y=2, status=TaskStatus.RUNNING)

    async def fake_update_task_cancelled(*_: object, **__: object) -> bool:
        return False

    async def fake_insert_credit_transaction(*_: object, **__: object) -> None:
        raise AssertionError("credit transaction should not be inserted when cancel is not applied")

    async def fake_refund_and_decrement_active(**_: object) -> str:
        raise AssertionError("refund should not be executed when cancel is not applied")

    monkeypatch.setattr(app_module, "get_task", fake_get_task)
    monkeypatch.setattr(app_module, "update_task_cancelled", fake_update_task_cancelled)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(app_module, "refund_and_decrement_active", fake_refund_and_decrement_active)

    response = client.post(
        f"/v1/task/{target_id}/cancel",
        headers={"Authorization": "Bearer key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
    client.close()


def test_cancel_refund_not_applied_when_ledger_write_fails(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    target_id = TASK_ID_SECONDARY
    refunds: list[tuple[UUID, int]] = []

    async def fake_get_task(*_: object, **__: object) -> TaskRecord:
        return _task(target_id, user.user_id, x=3, y=4, status=TaskStatus.PENDING)

    async def fake_update_task_cancelled(*_: object, **__: object) -> bool:
        return True

    async def fake_insert_credit_transaction(*_: object, **__: object) -> None:
        raise RuntimeError("audit sink down")

    async def fake_refund_and_decrement_active(**kwargs: object) -> str:
        refunds.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "sha-refund"

    monkeypatch.setattr(app_module, "get_task", fake_get_task)
    monkeypatch.setattr(app_module, "update_task_cancelled", fake_update_task_cancelled)
    monkeypatch.setattr(app_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        app_module,
        "refund_and_decrement_active",
        fake_refund_and_decrement_active,
    )

    response = client.post(
        f"/v1/task/{target_id}/cancel",
        headers={"Authorization": "Bearer key"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_DEGRADED"
    assert refunds == []
    client.close()


def test_webhook_registration_get_and_delete_paths(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    updated_at = datetime.now(tz=UTC)

    async def fake_upsert_webhook_subscription(*_: object, **kwargs: object) -> Any:
        return SimpleNamespace(
            user_id=kwargs["user_id"],
            callback_url=kwargs["callback_url"],
            enabled=kwargs["enabled"],
            updated_at=updated_at,
        )

    async def fake_get_webhook_subscription(*_: object, **__: object) -> Any:
        return SimpleNamespace(
            user_id=user.user_id,
            callback_url="https://example.com/webhook",
            enabled=True,
            updated_at=updated_at,
        )

    async def fake_disable_webhook_subscription(*_: object, **__: object) -> Any:
        return SimpleNamespace(
            user_id=user.user_id,
            callback_url="https://example.com/webhook",
            enabled=False,
            updated_at=updated_at,
        )

    monkeypatch.setattr(app_module, "upsert_webhook_subscription", fake_upsert_webhook_subscription)
    monkeypatch.setattr(app_module, "get_webhook_subscription", fake_get_webhook_subscription)
    monkeypatch.setattr(
        app_module,
        "disable_webhook_subscription",
        fake_disable_webhook_subscription,
    )

    put_response = client.put(
        V1_WEBHOOK_PATH,
        headers={"Authorization": "Bearer key"},
        json={"callback_url": "https://example.com/webhook", "enabled": True},
    )
    assert put_response.status_code == 200
    assert put_response.json()["enabled"] is True

    invalid_url = client.put(
        V1_WEBHOOK_PATH,
        headers={"Authorization": "Bearer key"},
        json={"callback_url": "ftp://invalid.example", "enabled": True},
    )
    assert invalid_url.status_code == 400

    get_response = client.get(V1_WEBHOOK_PATH, headers={"Authorization": "Bearer key"})
    assert get_response.status_code == 200
    assert get_response.json()["callback_url"] == "https://example.com/webhook"

    delete_response = client.delete(V1_WEBHOOK_PATH, headers={"Authorization": "Bearer key"})
    assert delete_response.status_code == 200
    assert delete_response.json()["enabled"] is False
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


def test_oauth_token_exchange_accepts_client_credentials(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    called: dict[str, str] = {}

    async def fake_exchange(
        *,
        client_id: str,
        client_secret: str,
        scope: str,
        request: Any,
    ) -> dict[str, Any]:
        called["client_id"] = client_id
        called["client_secret"] = client_secret
        called["scope"] = scope
        return {
            "access_token": "token-123",
            "token_type": "bearer",
            "expires_in": 600,
            "scope": scope,
        }

    monkeypatch.setattr(
        app_module, "_exchange_client_credentials_for_token", fake_exchange, raising=False
    )

    response = client.post(
        "/v1/oauth/token",
        json={"client_id": "solution1-user1", "client_secret": "solution1-user1-secret"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "token-123"
    assert called["client_id"] == "solution1-user1"
    client.close()


def test_oauth_token_exchange_rate_limited(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    async def fake_exchange(
        *,
        client_id: str,
        client_secret: str,
        scope: str,
        request: Any,
    ) -> dict[str, Any]:
        _ = (client_id, client_secret, scope, request)
        return {
            "access_token": "token-123",
            "token_type": "bearer",
            "expires_in": 600,
            "scope": "task:submit",
        }

    monkeypatch.setattr(
        app_module, "_exchange_client_credentials_for_token", fake_exchange, raising=False
    )
    runtime_settings = cast(Any, client).app.state.runtime.settings
    runtime_settings.oauth_token_rate_limit_enabled = True
    runtime_settings.oauth_token_rate_limit_window_seconds = 60
    runtime_settings.oauth_token_rate_limit_max_requests = 1

    first = client.post(
        "/v1/oauth/token",
        json={"client_id": "solution1-user1", "client_secret": "solution1-user1-secret"},
    )
    second = client.post(
        "/v1/oauth/token",
        json={"client_id": "solution1-user1", "client_secret": "solution1-user1-secret"},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert isinstance(second.json()["error"]["retry_after"], int)
    client.close()


def test_oauth_token_exchange_accepts_api_key_alias(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    called: dict[str, str] = {}

    async def fake_exchange(
        *,
        client_id: str,
        client_secret: str,
        scope: str,
        request: Any,
    ) -> dict[str, Any]:
        called["client_id"] = client_id
        called["client_secret"] = client_secret
        called["scope"] = scope
        return {
            "access_token": "token-api-key",
            "token_type": "bearer",
            "expires_in": 600,
            "scope": scope,
        }

    monkeypatch.setattr(
        app_module, "_exchange_client_credentials_for_token", fake_exchange, raising=False
    )

    async def _valid_api_key(**_: object) -> bool:
        return True

    monkeypatch.setattr(app_module, "_validate_oauth_api_key", _valid_api_key, raising=False)

    response = client.post("/v1/oauth/token", json={"api_key": DEFAULT_ALICE_API_KEY})
    assert response.status_code == 200
    assert called["client_id"] == "solution1-user1"
    assert response.json()["access_token"] == "token-api-key"
    client.close()


def test_oauth_token_exchange_rejects_unknown_api_key_alias(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    async def _invalid_api_key(**_: object) -> bool:
        return False

    monkeypatch.setattr(app_module, "_validate_oauth_api_key", _invalid_api_key, raising=False)

    response = client.post("/v1/oauth/token", json={"api_key": DEFAULT_ALICE_API_KEY})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    client.close()


def test_oauth_token_exchange_returns_503_when_api_key_validation_fails(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
    )

    async def _raise_validation_error(**_: object) -> bool:
        raise RuntimeError("validation unavailable")

    monkeypatch.setattr(
        app_module,
        "_validate_oauth_api_key",
        _raise_validation_error,
        raising=False,
    )

    response = client.post("/v1/oauth/token", json={"api_key": DEFAULT_ALICE_API_KEY})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_DEGRADED"
    client.close()


def test_auth_prefers_jwt_resolution_for_jwt_like_bearer(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    async def fake_resolve_api_key(**_: object) -> AuthUser:
        raise AssertionError("api key fallback should not run for jwt-like bearer token")

    async def fake_resolve_jwt(*, token: str, request: Any) -> AuthUser:
        assert token == "jwt.header.signature"
        _ = request
        return user

    monkeypatch.setattr(app_module, "resolve_user_from_api_key", fake_resolve_api_key)
    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_jwt, raising=False)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 404
    client.close()


def test_auth_returns_401_when_jwt_resolution_fails(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    async def fake_resolve_api_key(**_: object) -> AuthUser:
        raise AssertionError("api key fallback should not run for jwt-like bearer token")

    async def fake_resolve_jwt(*, token: str, request: Any) -> None:
        assert token == "jwt.header.signature"
        _ = request
        return None

    monkeypatch.setattr(app_module, "resolve_user_from_api_key", fake_resolve_api_key)
    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_jwt, raising=False)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    client.close()


def test_auth_revoke_endpoint_revokes_verified_token(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    async def fake_resolve_jwt(*, token: str, request: Any) -> AuthUser:
        assert token == "jwt.header.signature"
        request.state.jwt_claims = {
            "jti": "jwt-revoke-test",
            "exp": int((datetime.now(tz=UTC) + timedelta(hours=1)).timestamp()),
        }
        return user

    revoke_calls: list[tuple[str, UUID, int]] = []

    async def fake_revoke_jti(
        *,
        redis_client: Any,
        pool: Any,
        user_id: UUID,
        jti: str,
        expires_at: datetime,
        bucket_ttl: int,
    ) -> None:
        _ = (redis_client, pool)
        assert expires_at.tzinfo is not None
        revoke_calls.append((jti, user_id, bucket_ttl))

    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_jwt, raising=False)
    monkeypatch.setattr(app_module, "revoke_jti", fake_revoke_jti)

    response = client.post(
        V1_AUTH_REVOKE_PATH,
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 200
    assert response.json() == {"revoked": True}
    assert revoke_calls == [("jwt-revoke-test", TEST_USER_ID, 129_600)]
    client.close()


def test_auth_rejects_revoked_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    client_id = "solution1-user1"
    token_id = "revoked-token-id"
    today_key = f"revoked:{TEST_USER_ID}:{datetime.now(tz=UTC).date().isoformat()}"
    redis_client = _FakeRedisClient(
        hashes={},
        values={},
        sets={today_key: {token_id}},
    )
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    monkeypatch.setattr(app_module, "_jwks_client", lambda *_args, **_kwargs: _FakeJwksClient())

    def _fake_decode(*_: object, **__: object) -> dict[str, str]:
        return {
            "sub": client_id,
            "client_id": client_id,
            "jti": token_id,
        }

    monkeypatch.setattr(app_module.jwt, "decode", _fake_decode)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    client.close()


def test_auth_rejects_revoked_jwt_from_yesterday_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=100,
    )
    client_id = "solution1-user1"
    token_id = "revoked-yesterday-token-id"
    yesterday_key = (
        f"revoked:{TEST_USER_ID}:{(datetime.now(tz=UTC) - timedelta(days=1)).date().isoformat()}"
    )
    redis_client = _FakeRedisClient(
        hashes={},
        values={},
        sets={yesterday_key: {token_id}},
    )
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    monkeypatch.setattr(app_module, "_jwks_client", lambda *_args, **_kwargs: _FakeJwksClient())

    def _fake_decode(*_: object, **__: object) -> dict[str, str]:
        return {
            "sub": client_id,
            "client_id": client_id,
            "jti": token_id,
        }

    monkeypatch.setattr(app_module.jwt, "decode", _fake_decode)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    client.close()


def test_jwt_path_does_not_call_api_key_resolver(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    monkeypatch.setattr(app_module, "_jwks_client", lambda *_args, **_kwargs: _FakeJwksClient())

    async def _api_lookup_forbidden(**_: object) -> AuthUser:
        raise AssertionError("JWT path must not call resolve_user_from_api_key")

    def _fake_decode(*_: object, **__: object) -> dict[str, str]:
        return {
            "sub": "solution1-user1",
            "client_id": "solution1-user1",
            "jti": "jwt-path-no-db",
        }

    monkeypatch.setattr(app_module, "resolve_user_from_api_key", _api_lookup_forbidden)
    monkeypatch.setattr(app_module.jwt, "decode", _fake_decode)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 404
    client.close()


def test_auth_returns_401_for_expired_jwt(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    monkeypatch.setattr(app_module, "_jwks_client", lambda *_args, **_kwargs: _FakeJwksClient())

    def _expired_decode(*_: object, **__: object) -> dict[str, str]:
        raise app_module.jwt.ExpiredSignatureError("token expired")

    monkeypatch.setattr(app_module.jwt, "decode", _expired_decode)

    response = client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(TASK_ID_PRIMARY)},
        headers={"Authorization": "Bearer jwt.header.signature"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"
    client.close()


def test_admin_route_accepts_jwt_with_admin_role_claim(
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
    app_module, client = _build_app(
        monkeypatch,
        redis_client=redis_client,
        auth_user=user,
        bypass_auth=False,
    )

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    monkeypatch.setattr(app_module, "_jwks_client", lambda *_args, **_kwargs: _FakeJwksClient())

    def _fake_decode(*_: object, **__: object) -> dict[str, str]:
        return {
            "sub": "solution1-admin",
            "client_id": "solution1-admin",
            "role": "admin",
            "jti": "jti-admin",
            "scope": "admin:credits task:poll task:submit task:cancel",
        }

    monkeypatch.setattr(app_module.jwt, "decode", _fake_decode)

    async def fake_admin_update_user_credits(*_: object, **__: object) -> tuple[UUID, int]:
        return TEST_USER_ID, 1500

    monkeypatch.setattr(app_module, "admin_update_user_credits", fake_admin_update_user_credits)

    response = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": "Bearer jwt.header.signature"},
        json={"api_key": DEFAULT_ALICE_API_KEY, "delta": 5, "reason": "manual_topup"},
    )

    assert response.status_code == 200
    assert response.json()["new_balance"] == 1500
    client.close()
