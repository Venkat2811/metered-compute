from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient
from uuid6 import uuid7

from solution3.app import create_app
from solution3.constants import (
    BillingState,
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)
from solution3.core.runtime import RuntimeState
from solution3.core.settings import AppSettings
from solution3.models.domain import AuthUser, TaskCommand, TaskQueryView
from solution3.services.billing import ReserveCreditsResult

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from solution3.services.billing import TigerBeetleBilling


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes[key] = {**self.hashes.get(key, {}), **mapping}

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return None if value is None else str(value)

    async def incr(self, key: str) -> int:
        next_value = self.values.get(key, 0) + 1
        self.values[key] = next_value
        return next_value

    async def decr(self, key: str) -> int:
        next_value = max(0, self.values.get(key, 0) - 1)
        self.values[key] = next_value
        return next_value


class FakeBilling:
    def __init__(
        self,
        *,
        reserve_result: ReserveCreditsResult = ReserveCreditsResult.ACCEPTED,
        void_ok: bool = True,
    ) -> None:
        self.reserve_result = reserve_result
        self.void_ok = void_ok
        self.reserve_calls: list[tuple[UUID, UUID, int]] = []
        self.void_calls: list[UUID] = []

    def reserve_credits(
        self,
        *,
        user_id: UUID | str,
        transfer_id: UUID | str,
        amount: int,
    ) -> ReserveCreditsResult:
        self.reserve_calls.append((UUID(str(user_id)), UUID(str(transfer_id)), amount))
        return self.reserve_result

    def void_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        self.void_calls.append(UUID(str(pending_transfer_id)))
        return self.void_ok


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_name="mc-solution3",
        app_env="test",
        task_cost=10,
        task_result_ttl_seconds=86_400,
        max_concurrent_free=1,
        max_concurrent_pro=10,
        max_concurrent_enterprise=64,
        hydra_public_url="http://hydra:4444",
        oauth_request_timeout_seconds=3.0,
        admin_api_key="e1138140-6c35-49b6-b723-ba8d609d8eb5",
        alice_api_key="586f0ef6-e655-4413-ab08-a481db150389",
        bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        oauth_admin_client_id="solution3-admin",
        oauth_admin_client_secret="solution3-admin-secret",
        oauth_user1_client_id="solution3-user1",
        oauth_user1_client_secret="solution3-user1-secret",
        oauth_user2_client_id="solution3-user2",
        oauth_user2_client_secret="solution3-user2-secret",
        oauth_admin_tier=SubscriptionTier.ENTERPRISE,
        oauth_user1_tier=SubscriptionTier.PRO,
        oauth_user2_tier=SubscriptionTier.FREE,
        oauth_admin_user_id=UUID("5ba7f2f8-24be-448a-9552-3af6e06e8898"),
        oauth_user1_user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        oauth_user2_user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        log_leak_sensitive_values=False,
    )


def _auth_user(*, role: UserRole = UserRole.USER) -> AuthUser:
    return AuthUser(
        api_key="586f0ef6-e655-4413-ab08-a481db150389",
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        name="test-user",
        role=role,
        tier=SubscriptionTier.PRO if role == UserRole.USER else SubscriptionTier.ENTERPRISE,
        scopes=frozenset({"task:submit", "task:poll", "task:cancel", "admin:credits"}),
    )


def _task_command(
    *,
    task_id: UUID,
    user_id: UUID,
    status: TaskStatus,
    billing_state: BillingState,
    x: int = 2,
    y: int = 3,
    idempotency_key: str | None = "idem-1",
) -> TaskCommand:
    now = datetime.now(tz=UTC)
    return TaskCommand(
        task_id=task_id,
        user_id=user_id,
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=status,
        billing_state=billing_state,
        x=x,
        y=y,
        cost=10,
        tb_pending_transfer_id=uuid7(),
        callback_url=None,
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )


def _query_view(*, task_id: UUID, user_id: UUID) -> TaskQueryView:
    now = datetime.now(tz=UTC)
    return TaskQueryView(
        task_id=task_id,
        user_id=user_id,
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=TaskStatus.RUNNING,
        billing_state=BillingState.RESERVED,
        result=None,
        error=None,
        runtime_ms=None,
        projection_version=1,
        created_at=now,
        updated_at=now,
    )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_user: AuthUser | None = None,
    redis_client: FakeRedis | None = None,
    billing_client: FakeBilling | None = None,
) -> tuple[TestClient, FakeRedis, FakeBilling]:
    from solution3.services import auth as auth_module

    app = create_app()
    fake_redis = redis_client or FakeRedis()
    fake_billing = billing_client or FakeBilling()
    runtime_settings = cast(AppSettings, _settings())
    app.state.runtime = RuntimeState(
        settings=runtime_settings,
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast("Redis[str]", fake_redis),
        billing_client=cast("TigerBeetleBilling", fake_billing),
        started=True,
    )

    async def _fake_auth() -> AuthUser:
        assert current_user is not None
        return current_user

    if current_user is not None:
        app.dependency_overrides[auth_module.require_authenticated_user] = _fake_auth

    return TestClient(app, raise_server_exceptions=False), fake_redis, fake_billing


def test_oauth_token_exchanges_api_key_for_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import auth_routes

    async def fake_validate_api_key(*, api_key: str, request: object) -> bool:
        assert api_key == "586f0ef6-e655-4413-ab08-a481db150389"
        _ = request
        return True

    async def fake_exchange(
        *, client_id: str, client_secret: str, scope: str, request: object
    ) -> dict[str, object]:
        assert client_id == "solution3-user1"
        assert client_secret == "solution3-user1-secret"
        assert "task:submit" in scope
        _ = request
        return {
            "access_token": "jwt.header.signature",
            "token_type": "bearer",
            "expires_in": 3600,
            "scope": scope,
        }

    monkeypatch.setattr(auth_routes, "_validate_oauth_api_key", fake_validate_api_key)
    monkeypatch.setattr(auth_routes, "_exchange_client_credentials_for_token", fake_exchange)

    client, _, _ = _client(monkeypatch)
    with client:
        response = client.post(
            "/v1/oauth/token",
            json={"api_key": "586f0ef6-e655-4413-ab08-a481db150389"},
        )

    assert response.status_code == 200
    assert response.json()["access_token"] == "jwt.header.signature"


def test_submit_requires_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _client(monkeypatch)
    with client:
        response = client.post("/v1/task", json={"x": 1, "y": 2})

    assert response.status_code == 401


def test_submit_creates_pending_command_and_caches_hot_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_write_routes

    created_task = _task_command(
        task_id=uuid7(),
        user_id=_auth_user().user_id,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
    )

    async def fake_submit(*_: object, **__: object) -> task_write_routes.SubmitCommandResult:
        return task_write_routes.SubmitCommandResult(created=True, command=created_task)

    monkeypatch.setattr(task_write_routes, "submit_task_command", fake_submit)

    client, redis_client, billing = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.post(
            "/v1/task",
            headers={"Authorization": "Bearer jwt.header.signature", "Idempotency-Key": "idem-123"},
            json={"x": 1, "y": 2},
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "PENDING"
    assert payload["billing_state"] == "RESERVED"
    assert redis_client.hashes[f"task:{created_task.task_id}"]["status"] == "PENDING"
    assert len(billing.reserve_calls) == 1


def test_submit_idempotent_replay_returns_existing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_write_routes

    existing = _task_command(
        task_id=uuid7(),
        user_id=_auth_user().user_id,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
        x=1,
        y=2,
    )

    async def fake_submit(*_: object, **__: object) -> task_write_routes.SubmitCommandResult:
        return task_write_routes.SubmitCommandResult(created=False, command=existing)

    monkeypatch.setattr(task_write_routes, "submit_task_command", fake_submit)

    client, _, billing = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.post(
            "/v1/task",
            headers={"Authorization": "Bearer jwt.header.signature", "Idempotency-Key": "idem-123"},
            json={"x": 1, "y": 2},
        )

    assert response.status_code == 200
    assert response.json()["task_id"] == str(existing.task_id)
    assert len(billing.reserve_calls) == 1
    assert len(billing.void_calls) == 1


def test_poll_returns_hot_path_task_state_from_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid7()
    redis_client = FakeRedis()
    redis_client.hashes[f"task:{task_id}"] = {
        "user_id": str(_auth_user().user_id),
        "status": "RUNNING",
        "billing_state": "RESERVED",
    }

    client, _, _ = _client(monkeypatch, current_user=_auth_user(), redis_client=redis_client)
    with client:
        response = client.get(
            "/v1/poll",
            params={"task_id": str(task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"


def test_poll_returns_not_found_for_foreign_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid7()
    redis_client = FakeRedis()
    redis_client.hashes[f"task:{task_id}"] = {
        "user_id": str(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        "status": "RUNNING",
        "billing_state": "RESERVED",
    }

    client, _, _ = _client(monkeypatch, current_user=_auth_user(), redis_client=redis_client)
    with client:
        response = client.get(
            "/v1/poll",
            params={"task_id": str(task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_poll_falls_back_to_query_view_when_cache_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_read_routes

    task_id = uuid7()
    query_view = _query_view(task_id=task_id, user_id=_auth_user().user_id)

    async def fake_get_query_view(*_: object, **__: object) -> TaskQueryView:
        return query_view

    async def fake_get_command(*_: object, **__: object) -> None:
        raise AssertionError("command lookup should not run when query view exists")

    monkeypatch.setattr(task_read_routes, "get_task_query_view", fake_get_query_view)
    monkeypatch.setattr(task_read_routes, "get_task_command", fake_get_command)

    client, _, _ = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.get(
            "/v1/poll",
            params={"task_id": str(task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"


def test_poll_falls_back_to_command_store_when_projection_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_read_routes

    task_id = uuid7()
    command = _task_command(
        task_id=task_id,
        user_id=_auth_user().user_id,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
    )

    async def fake_get_query_view(*_: object, **__: object) -> None:
        return None

    async def fake_get_command(*_: object, **__: object) -> TaskCommand:
        return command

    monkeypatch.setattr(task_read_routes, "get_task_query_view", fake_get_query_view)
    monkeypatch.setattr(task_read_routes, "get_task_command", fake_get_command)

    client, _, _ = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.get(
            "/v1/poll",
            params={"task_id": str(task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


def test_poll_returns_service_degraded_when_cache_misses_and_db_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = _client(monkeypatch, current_user=_auth_user())
    with client:
        cast(Any, client.app).state.runtime.db_pool = None
        response = client.get(
            "/v1/poll",
            params={"task_id": str(uuid7())},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_DEGRADED"


def test_cancel_conflict_on_terminal_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_write_routes

    async def fake_cancel(*_: object, **__: object) -> bool:
        return False

    async def fake_get_task_command(*_: object, **__: object) -> TaskCommand:
        return _task_command(
            task_id=uuid7(),
            user_id=_auth_user().user_id,
            status=TaskStatus.COMPLETED,
            billing_state=BillingState.CAPTURED,
        )

    monkeypatch.setattr(task_write_routes, "cancel_task_command", fake_cancel)
    monkeypatch.setattr(task_write_routes, "get_task_command", fake_get_task_command)

    client, _, _ = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.post(
            f"/v1/task/{uuid7()}/cancel",
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 409


def test_cancel_returns_not_found_for_foreign_owned_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_write_routes

    async def fake_get_task_command(*_: object, **__: object) -> TaskCommand:
        return _task_command(
            task_id=uuid7(),
            user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            status=TaskStatus.PENDING,
            billing_state=BillingState.RESERVED,
        )

    monkeypatch.setattr(task_write_routes, "get_task_command", fake_get_task_command)

    client, _, _ = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.post(
            f"/v1/task/{uuid7()}/cancel",
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_admin_credits_forbidden_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = _client(monkeypatch, current_user=_auth_user(role=UserRole.USER))
    with client:
        response = client.post(
            "/v1/admin/credits",
            headers={"Authorization": "Bearer jwt.header.signature"},
            json={
                "api_key": "586f0ef6-e655-4413-ab08-a481db150389",
                "amount": 10,
                "reason": "test",
            },
        )

    assert response.status_code == 403


def test_submit_returns_402_when_tigerbeetle_reserve_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, billing = _client(
        monkeypatch,
        current_user=_auth_user(),
        billing_client=FakeBilling(
            reserve_result=ReserveCreditsResult.INSUFFICIENT_CREDITS,
        ),
    )
    with client:
        response = client.post(
            "/v1/task",
            headers={"Authorization": "Bearer jwt.header.signature", "Idempotency-Key": "idem-123"},
            json={"x": 1, "y": 2},
        )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "INSUFFICIENT_CREDITS"
    assert len(billing.reserve_calls) == 1


def test_cancel_voids_pending_transfer_before_command_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solution3.api import task_write_routes

    command = _task_command(
        task_id=uuid7(),
        user_id=_auth_user().user_id,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
    )

    async def fake_get_task_command(*_: object, **__: object) -> TaskCommand:
        return command

    async def fake_cancel(*_: object, **__: object) -> bool:
        return True

    monkeypatch.setattr(task_write_routes, "get_task_command", fake_get_task_command)
    monkeypatch.setattr(task_write_routes, "cancel_task_command", fake_cancel)

    client, _, billing = _client(monkeypatch, current_user=_auth_user())
    with client:
        response = client.post(
            f"/v1/task/{command.task_id}/cancel",
            headers={"Authorization": "Bearer jwt.header.signature"},
        )

    assert response.status_code == 200
    assert billing.void_calls == [command.tb_pending_transfer_id]
