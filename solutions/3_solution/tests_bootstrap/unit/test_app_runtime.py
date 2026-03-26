from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient

from solution3 import app as app_module
from solution3.app import create_app
from solution3.constants import SubscriptionTier
from solution3.core.runtime import RuntimeState
from solution3.models.domain import AuthUser
from solution3.services import auth as auth_service


class FakeRedisClient:
    def __init__(self) -> None:
        self.ping_calls = 0
        self.close_calls = 0

    async def ping(self) -> None:
        self.ping_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class FakePool:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_name="mc-solution3",
        app_env="test",
        log_leak_sensitive_values=False,
        postgres_dsn="postgresql://db",
        redis_url="redis://redis:6379/0",
        task_result_ttl_seconds=86_400,
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
        tigerbeetle_cluster_id=0,
        tigerbeetle_endpoint="tigerbeetle:3000",
        tigerbeetle_ledger_id=1,
        tigerbeetle_revenue_account_id=10,
        tigerbeetle_escrow_account_id=20,
        tigerbeetle_pending_transfer_timeout_seconds=600,
    )


@pytest.mark.asyncio
async def test_build_runtime_runs_migrations_bootstraps_and_pings_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrations_run: list[str] = []
    pool = FakePool()
    redis = FakeRedisClient()
    billing_client = object()

    async def fake_run_migrations(dsn: str) -> None:
        migrations_run.append(dsn)

    async def fake_create_pool(*, dsn: str) -> FakePool:
        assert dsn == "postgresql://db"
        return pool

    async def fake_bootstrap_tigerbeetle(*, db_pool: object, settings: object) -> object:
        assert db_pool is pool
        assert settings == _settings()
        return billing_client

    monkeypatch.setattr(app_module, "run_migrations", fake_run_migrations)
    monkeypatch.setattr("solution3.app.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.app.Redis.from_url",
        lambda url, decode_responses: redis,
    )
    monkeypatch.setattr(app_module, "_bootstrap_tigerbeetle", fake_bootstrap_tigerbeetle)

    runtime = await app_module._build_runtime(settings=cast(Any, _settings()))

    assert migrations_run == ["postgresql://db"]
    assert runtime.db_pool is pool
    assert cast(Any, runtime.redis_client) is redis
    assert runtime.billing_client is billing_client
    assert redis.ping_calls == 1


@pytest.mark.asyncio
async def test_close_runtime_closes_redis_and_db_pool() -> None:
    pool = FakePool()
    redis = FakeRedisClient()
    runtime = RuntimeState(
        settings=cast(Any, _settings()),
        db_pool=cast(asyncpg.Pool, pool),
        redis_client=cast(Any, redis),
        billing_client=None,
        started=True,
    )

    await app_module._close_runtime(runtime)

    assert redis.close_calls == 1
    assert pool.close_calls == 1


@pytest.mark.asyncio
async def test_bootstrap_tigerbeetle_initializes_seed_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    billing_calls: list[tuple[str, object, int | None]] = []
    settings = cast(Any, _settings())

    class FakeBilling:
        def ensure_platform_accounts(self) -> None:
            billing_calls.append(("platform", None, None))

        def ensure_user_account(self, user_id: UUID, *, initial_credits: int = 0) -> None:
            billing_calls.append(("user", user_id, initial_credits))

    async def fake_list_active_users_with_initial_credits(
        db_pool: object,
    ) -> list[tuple[UUID, int]]:
        assert db_pool is not None
        return [
            (UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), 1000),
            (UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), 250),
        ]

    async def fake_to_thread(func: object, *args: object, **kwargs: object) -> object:
        return cast(Any, func)(*args, **kwargs)

    monkeypatch.setattr(app_module, "_build_billing", lambda _settings: FakeBilling())
    monkeypatch.setattr(
        app_module,
        "list_active_users_with_initial_credits",
        fake_list_active_users_with_initial_credits,
    )
    monkeypatch.setattr("solution3.app.asyncio.to_thread", fake_to_thread)

    billing = await app_module._bootstrap_tigerbeetle(
        db_pool=cast(asyncpg.Pool, object()),
        settings=settings,
    )

    assert isinstance(billing, FakeBilling)
    assert billing_calls == [
        ("platform", None, None),
        ("user", UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), 1000),
        ("user", UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), 250),
    ]


def test_create_app_uses_lifespan_and_validation_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    built_runtime = RuntimeState(settings=cast(Any, _settings()), started=False)
    build_calls = 0
    close_calls: list[RuntimeState] = []

    async def fake_build_runtime(*, settings: object) -> RuntimeState:
        nonlocal build_calls
        build_calls += 1
        assert settings == _settings()
        return built_runtime

    async def fake_close_runtime(runtime: RuntimeState) -> None:
        close_calls.append(runtime)

    async def fake_auth() -> AuthUser:
        return AuthUser(
            api_key="key",
            user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            name="user",
            role=cast(Any, "user"),
            tier=SubscriptionTier.PRO,
            scopes=frozenset({"task:submit"}),
        )

    monkeypatch.setattr(app_module, "load_settings", _settings)
    monkeypatch.setattr(app_module, "configure_logging", lambda *, enable_sensitive: None)
    monkeypatch.setattr(app_module, "_build_runtime", fake_build_runtime)
    monkeypatch.setattr(app_module, "_close_runtime", fake_close_runtime)

    app = create_app(initialize_runtime=True)
    app.dependency_overrides[auth_service.require_authenticated_user] = fake_auth

    with TestClient(app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        bad_request = client.post(
            "/v1/task",
            headers={"Authorization": "Bearer token"},
            json={"x": "bad", "y": 2},
        )

    assert health.status_code == 200
    assert health.json()["solution"] == "3_solution"
    assert bad_request.status_code == 400
    assert bad_request.json()["error"]["code"] == "BAD_REQUEST"
    assert build_calls == 1
    assert close_calls == [built_runtime]
