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

from solution2.constants import ModelClass, RequestMode, SubscriptionTier, TaskStatus, UserRole
from solution2.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_AUTH_CACHE_TTL_SECONDS,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_PENDING_MARKER_TTL_SECONDS,
    DEFAULT_TASK_COST,
    DEFAULT_TASK_RESULT_TTL_SECONDS,
    DEFAULT_USER1_API_KEY,
)
from solution2.models.domain import AuthUser, TaskCommand
from solution2.models.schemas import OAuthTokenRequest
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


def _request_for(app: FastAPI) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "app": app,
    }
    return Request(scope)


def _request_with_auth(app: FastAPI, authorization: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"authorization", authorization.encode("utf-8"))],
        "app": app,
    }
    return Request(scope)


def _load_app_module() -> Any:
    module = cast(Any, importlib.import_module("solution2.app"))
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
async def test_is_token_revoked_uses_postgres_fallback_on_redis_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()

    class _FailingRedis:
        def pipeline(self, *, transaction: bool = False) -> object:
            _ = transaction
            raise RuntimeError("redis pipeline down")

        async def sismember(self, _: str, __: str) -> int:
            raise RuntimeError("redis unavailable")

        async def sadd(self, key: str, value: str) -> int:
            write_through_calls.append((key, value))
            return 1

        async def expire(self, key: str, ttl: int) -> bool:
            expiry_calls.append((key, ttl))
            return True

    write_through_calls: list[tuple[str, str]] = []
    expiry_calls: list[tuple[str, int]] = []
    runtime = SimpleNamespace(
        redis_client=_FailingRedis(),
        db_pool=object(),
        settings=SimpleNamespace(revocation_bucket_ttl_seconds=129_600),
    )

    async def fake_is_jti_revoked(*_: object, **kwargs: object) -> bool:
        assert kwargs["jti"] == "revoked-jti"
        return True

    monkeypatch.setattr(app_module, "is_jti_revoked", fake_is_jti_revoked)

    revoked = await app_module._is_token_revoked(
        runtime=runtime,
        user_id=TEST_USER_ID,
        jti="revoked-jti",
    )

    assert revoked is True
    today = datetime.now(tz=UTC).date().isoformat()
    expected_key = f"revoked:{TEST_USER_ID}:{today}"
    assert write_through_calls == [(expected_key, "revoked-jti")]
    assert expiry_calls == [(expected_key, 129_600)]


@pytest.mark.asyncio
async def test_rehydrate_revocation_cache_populates_redis_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()

    class _RedisPipeline:
        def __init__(self) -> None:
            self.ops: list[tuple[str, str, str | int]] = []

        async def __aenter__(self) -> _RedisPipeline:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        def sadd(self, key: str, value: str) -> _RedisPipeline:
            self.ops.append(("sadd", key, value))
            return self

        def expire(self, key: str, ttl: int) -> _RedisPipeline:
            self.ops.append(("expire", key, ttl))
            return self

        async def execute(self) -> list[int]:
            return [1 for _ in self.ops]

    class _RedisWithPipeline:
        def __init__(self) -> None:
            self.pipeline_instance = _RedisPipeline()

        def pipeline(self, *, transaction: bool = False) -> _RedisPipeline:
            _ = transaction
            return self.pipeline_instance

    async def fake_load_active(*_: object, **__: object) -> list[tuple[str, UUID, str]]:
        return [
            ("jti-a", TEST_USER_ID, "2026-02-17"),
            ("jti-b", ALT_USER_ID, "2026-02-16"),
        ]

    monkeypatch.setattr(app_module, "load_active_revoked_jtis", fake_load_active)
    redis_client = _RedisWithPipeline()

    count = await app_module._rehydrate_revocation_cache(
        db_pool=cast(Any, object()),
        redis_client=cast(Any, redis_client),
        bucket_ttl_seconds=129_600,
    )

    assert count == 2
    assert redis_client.pipeline_instance.ops == [
        ("sadd", f"revoked:{TEST_USER_ID}:2026-02-17", "jti-a"),
        ("expire", f"revoked:{TEST_USER_ID}:2026-02-17", 129_600),
        ("sadd", f"revoked:{ALT_USER_ID}:2026-02-16", "jti-b"),
        ("expire", f"revoked:{ALT_USER_ID}:2026-02-16", 129_600),
    ]


@pytest.mark.asyncio
async def test_oauth_token_rate_limit_enforced() -> None:
    app_module = _load_app_module()
    app = FastAPI()

    class _RateLimitRedis:
        def __init__(self) -> None:
            self.counts: dict[str, int] = {}

        async def incr(self, key: str) -> int:
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        async def expire(self, key: str, _: int) -> bool:
            return key in self.counts

    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            oauth_token_rate_limit_enabled=True,
            oauth_token_rate_limit_window_seconds=60,
            oauth_token_rate_limit_max_requests=1,
        ),
        redis_client=_RateLimitRedis(),
    )
    app.state.runtime = runtime

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/oauth/token",
            "headers": [],
            "client": ("127.0.0.1", 10000),
            "app": app,
        }
    )
    payload = OAuthTokenRequest(client_id="solution2-user1", client_secret="secret")

    first = await app_module._check_oauth_token_rate_limit(payload=payload, request=request)
    second = await app_module._check_oauth_token_rate_limit(payload=payload, request=request)

    assert first is None
    assert isinstance(second, int)
    assert 1 <= second <= 60


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
        db_pool_min_size=1,
        db_pool_max_size=2,
        db_pool_command_timeout_seconds=1.0,
        db_statement_timeout_ms=50,
        db_idle_in_transaction_timeout_ms=500,
        redis_socket_timeout_seconds=0.05,
        redis_socket_connect_timeout_seconds=0.05,
        db_pool_max_inactive_connection_lifetime_seconds=1.0,
        revocation_bucket_ttl_seconds=129_600,
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

    async def fake_rehydrate_ok(**_: object) -> int:
        return 0

    monkeypatch.setattr(app_module, "_rehydrate_revocation_cache", fake_rehydrate_ok)
    monkeypatch.setattr(
        app_module,
        "build_dependency_health_service",
        lambda *_args, **_kwargs: SimpleNamespace(readiness=lambda: None),
    )

    async with app_module._lifespan(app):
        assert app.state.runtime.admission_script_sha == ""
        assert app.state.runtime.decrement_script_sha == ""
        assert app.state.runtime.db_pool is fake_pool
        assert app.state.runtime.redis_client is fake_redis

    assert fake_pool.closed is True
    assert fake_redis.closed is True


@pytest.mark.asyncio
async def test_lifespan_fails_closed_when_revocation_rehydrate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    app = FastAPI()

    settings = SimpleNamespace(
        app_name="test-api",
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
        redis_url="redis://localhost:6379/0",
        db_pool_min_size=1,
        db_pool_max_size=2,
        db_pool_command_timeout_seconds=1.0,
        db_statement_timeout_ms=50,
        db_idle_in_transaction_timeout_ms=500,
        redis_socket_timeout_seconds=0.05,
        redis_socket_connect_timeout_seconds=0.05,
        db_pool_max_inactive_connection_lifetime_seconds=1.0,
        revocation_bucket_ttl_seconds=129_600,
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

    async def fake_rehydrate(**_: object) -> int:
        raise RuntimeError("rehydrate failed")

    monkeypatch.setattr(app_module, "load_settings", lambda: settings)
    monkeypatch.setattr(app_module, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(app_module.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(app_module.Redis, "from_url", lambda *_args, **_kwargs: fake_redis)
    monkeypatch.setattr(app_module, "_rehydrate_revocation_cache", fake_rehydrate)

    with pytest.raises(RuntimeError):
        async with app_module._lifespan(app):
            raise AssertionError("lifespan should fail before yielding")

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
        scopes=frozenset({"task:submit", "task:poll", "task:cancel", "admin:credits"}),
    )

    async def fake_resolve_user(**_: object) -> AuthUser:
        return user

    async def fake_ready() -> object:
        return SimpleNamespace(ready=True, dependencies={"postgres": True, "redis": True})

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
            reservation_ttl_seconds=300,
            task_result_ttl_seconds=DEFAULT_TASK_RESULT_TTL_SECONDS,
            redis_task_state_ttl_seconds=86400,
            auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
            readiness_worker_timeout_seconds=1.0,
            admin_api_key=DEFAULT_ADMIN_API_KEY,
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
            xlen=lambda *_args, **_kwargs: 0,
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

    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_user)

    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        no_auth = client.post(V1_TASK_SUBMIT_PATH, json={"x": 1, "y": 1})
        assert no_auth.status_code == 401

        async def gate_exception(**_: object) -> tuple[object, str]:
            raise RuntimeError("gate down")

        monkeypatch.setattr(app_module, "run_admission_gate", gate_exception)
        down = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": "Bearer jwt.header.signature"},
            json={"x": 1, "y": 1},
        )
        assert down.status_code == 503

        async def retry_exception(**_: object) -> tuple[object, str]:
            raise RuntimeError("still down")

        monkeypatch.setattr(app_module, "run_admission_gate", retry_exception)
        retry_down = client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": "Bearer jwt.header.signature"},
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
        scopes=frozenset({"task:submit", "task:poll", "task:cancel"}),
    )

    async def fake_resolve_user(**_: object) -> AuthUser:
        return user

    class _FakeDependency:
        async def readiness(self) -> object:
            return SimpleNamespace(ready=True, dependencies={"postgres": True, "redis": True})

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
            reservation_ttl_seconds=300,
            task_result_ttl_seconds=5,
            redis_task_state_ttl_seconds=86400,
            auth_cache_ttl_seconds=DEFAULT_AUTH_CACHE_TTL_SECONDS,
            readiness_worker_timeout_seconds=1.0,
            admin_api_key=DEFAULT_ADMIN_API_KEY,
        ),
        db_pool=SimpleNamespace(acquire=lambda: _FakeAcquire()),
        redis_client=SimpleNamespace(
            hgetall=fake_hgetall,
            llen=fake_llen,
            xlen=fake_llen,
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
    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", fake_resolve_user)

    pending_task = TaskCommand(
        task_id=uuid4(),
        user_id=user.user_id,
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=TaskStatus.PENDING,
        x=1,
        y=2,
        cost=DEFAULT_TASK_COST,
        callback_url=None,
        idempotency_key=None,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )

    async def fake_get_task_query_view(_: object, __: UUID) -> None:
        return None

    async def fake_get_task_command(_: object, __: UUID) -> TaskCommand:
        return pending_task

    monkeypatch.setattr(app_module, "get_task_query_view", fake_get_task_query_view)
    monkeypatch.setattr(app_module, "get_task_command", fake_get_task_command)

    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        polled = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": str(pending_task.task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )
        assert polled.status_code == 200
        assert polled.json()["queue_position"] is None

        other_task = replace(
            pending_task,
            user_id=ALT_USER_ID,
            updated_at=datetime.now(tz=UTC),
        )

        async def fake_get_other(_: object, __: UUID) -> TaskCommand:
            return other_task

        monkeypatch.setattr(app_module, "get_task_command", fake_get_other)
        hidden = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": str(pending_task.task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )
        assert hidden.status_code == 404

        async def fake_get_completed(_: object, __: UUID) -> TaskCommand:
            return replace(
                pending_task,
                status=TaskStatus.COMPLETED,
                updated_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
            )

        monkeypatch.setattr(app_module, "get_task_command", fake_get_completed)
        expired = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": str(pending_task.task_id)},
            headers={"Authorization": "Bearer jwt.header.signature"},
        )
        assert expired.status_code == 200
        assert expired.json()["status"] == "EXPIRED"

        async def fake_admin_none(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(app_module, "admin_update_user_credits", fake_admin_none)
        forbidden_admin = client.post(
            V1_ADMIN_CREDITS_PATH,
            headers={"Authorization": "Bearer jwt.header.signature"},
            json={"api_key": user.api_key, "delta": 10, "reason": "topup"},
        )
        assert forbidden_admin.status_code == 403


@pytest.mark.asyncio
async def test_authenticate_jwt_only_failure_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(auth_cache_ttl_seconds=60),
        redis_client=SimpleNamespace(),
        db_pool=SimpleNamespace(),
    )

    jwt_request = _request_with_auth(app, "Bearer jwt.header.signature")

    async def _jwt_boom(**_: object) -> None:
        raise RuntimeError("jwt down")

    monkeypatch.setattr(app_module, "resolve_user_from_jwt_token", _jwt_boom)

    with pytest.raises(app_module.HTTPException) as jwt_exc:
        await app_module._authenticate(jwt_request)
    assert jwt_exc.value.status_code == 503

    token_request = _request_with_auth(app, "Bearer plain-api-key")
    with pytest.raises(app_module.HTTPException) as unauthorized_exc:
        await app_module._authenticate(token_request)
    assert unauthorized_exc.value.status_code == 401


def test_jwks_factory_and_oauth_principal_mapping() -> None:
    app_module = _load_app_module()
    jwks_client = app_module._jwks_client(
        "http://localhost:4444/.well-known/jwks.json",
        cache_ttl_seconds=300.0,
    )
    assert isinstance(jwks_client, app_module.jwt.PyJWKClient)

    settings = SimpleNamespace(
        oauth_admin_client_id="solution2-admin",
        oauth_user1_client_id="solution2-user1",
        oauth_user2_client_id="solution2-user2",
        oauth_admin_tier=SubscriptionTier.ENTERPRISE,
        oauth_user1_tier=SubscriptionTier.PRO,
        oauth_user2_tier=SubscriptionTier.FREE,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
        alice_api_key=DEFAULT_USER1_API_KEY,
        bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        oauth_admin_user_id=UUID("5ba7f2f8-24be-448a-9552-3af6e06e8898"),
        oauth_user1_user_id=TEST_USER_ID,
        oauth_user2_user_id=ALT_USER_ID,
    )

    assert (
        app_module._oauth_principal_for_client(client_id="solution2-admin", settings=settings).role
        == UserRole.ADMIN
    )
    assert (
        app_module._oauth_principal_for_client(client_id="solution2-user1", settings=settings).role
        == UserRole.USER
    )
    assert (
        app_module._oauth_principal_for_client(client_id="solution2-user2", settings=settings).role
        == UserRole.USER
    )
    assert app_module._oauth_principal_for_client(client_id="unknown", settings=settings) is None


@pytest.mark.asyncio
async def test_resolve_user_from_jwt_token_branch_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    settings = SimpleNamespace(
        hydra_jwks_url="http://hydra:4444/.well-known/jwks.json",
        hydra_issuer="http://hydra:4444/",
        hydra_jwks_cache_ttl_seconds=300.0,
        hydra_expected_audience=None,
        oauth_admin_client_id="solution2-admin",
        oauth_user1_client_id="solution2-user1",
        oauth_user2_client_id="solution2-user2",
        oauth_admin_tier=SubscriptionTier.ENTERPRISE,
        oauth_user1_tier=SubscriptionTier.PRO,
        oauth_user2_tier=SubscriptionTier.FREE,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
        alice_api_key=DEFAULT_USER1_API_KEY,
        bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        oauth_admin_user_id=UUID("5ba7f2f8-24be-448a-9552-3af6e06e8898"),
        oauth_user1_user_id=TEST_USER_ID,
        oauth_user2_user_id=ALT_USER_ID,
    )

    class _RedisNoPipeline:
        async def sismember(self, _: str, __: str) -> int:
            return 0

    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        settings=settings,
        redis_client=_RedisNoPipeline(),
        db_pool=SimpleNamespace(),
    )
    request = _request_for(app)

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    monkeypatch.setattr(
        app_module,
        "_jwks_client",
        lambda *_args, **_kwargs: _FakeJwksClient(),
    )

    def _decode_invalid(*_: object, **__: object) -> dict[str, str]:
        raise app_module.jwt.InvalidTokenError("invalid")

    monkeypatch.setattr(app_module.jwt, "decode", _decode_invalid)
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    def _decode_error(*_: object, **__: object) -> dict[str, str]:
        raise RuntimeError("decode down")

    monkeypatch.setattr(app_module.jwt, "decode", _decode_error)
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(app_module.jwt, "decode", lambda *_a, **_k: {})
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(
        app_module.jwt,
        "decode",
        lambda *_a, **_k: {"client_id": "unknown", "sub": "unknown"},
    )
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(
        app_module.jwt,
        "decode",
        lambda *_a, **_k: {"client_id": "solution2-user1", "sub": "other-sub"},
    )
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(
        app_module.jwt,
        "decode",
        lambda *_a, **_k: {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            "role": "not-a-role",
        },
    )
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(
        app_module.jwt,
        "decode",
        lambda *_a, **_k: {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            "tier": "not-a-tier",
        },
    )
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(
        app_module.jwt,
        "decode",
        lambda *_a, **_k: {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            "tier": SubscriptionTier.ENTERPRISE.value,
        },
    )
    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None

    monkeypatch.setattr(
        app_module.jwt,
        "decode",
        lambda *_a, **_k: {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            "role": UserRole.USER.value,
            "tier": SubscriptionTier.PRO.value,
            "jti": "coverage-token-valid",
        },
    )
    resolved = await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request)
    assert resolved is not None
    assert resolved.role == UserRole.USER
    assert resolved.tier == SubscriptionTier.PRO

    settings.hydra_expected_audience = "solution2-api"

    def _decode_with_aud(*_: object, **kwargs: object) -> dict[str, str]:
        assert kwargs["audience"] == "solution2-api"
        options = cast(dict[str, object], kwargs["options"])
        assert options["verify_aud"] is True
        return {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            "role": UserRole.USER.value,
            "tier": SubscriptionTier.PRO.value,
            "jti": "audience-token-valid",
        }

    monkeypatch.setattr(app_module.jwt, "decode", _decode_with_aud)
    resolved_with_aud = await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request)
    assert resolved_with_aud is not None


@pytest.mark.asyncio
async def test_resolve_user_from_jwt_token_rejects_missing_jti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    settings = SimpleNamespace(
        hydra_jwks_url="http://hydra:4444/.well-known/jwks.json",
        hydra_issuer="http://hydra:4444/",
        hydra_jwks_cache_ttl_seconds=300.0,
        hydra_expected_audience=None,
        oauth_admin_client_id="solution2-admin",
        oauth_user1_client_id="solution2-user1",
        oauth_user2_client_id="solution2-user2",
        oauth_admin_tier=SubscriptionTier.ENTERPRISE,
        oauth_user1_tier=SubscriptionTier.PRO,
        oauth_user2_tier=SubscriptionTier.FREE,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
        alice_api_key=DEFAULT_USER1_API_KEY,
        bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        oauth_admin_user_id=UUID("5ba7f2f8-24be-448a-9552-3af6e06e8898"),
        oauth_user1_user_id=TEST_USER_ID,
        oauth_user2_user_id=ALT_USER_ID,
    )

    app = FastAPI()

    async def _not_revoked(_: str, __: str) -> int:
        return 0

    app.state.runtime = SimpleNamespace(
        settings=settings,
        redis_client=SimpleNamespace(sismember=_not_revoked),
    )
    request = _request_for(app)

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        @staticmethod
        def get_signing_key_from_jwt(_: str) -> _FakeSigningKey:
            return _FakeSigningKey()

    def _decode_without_jti(*_: object, **__: object) -> dict[str, object]:
        return {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            # no jti included
        }

    monkeypatch.setattr(
        app_module,
        "_jwks_client",
        lambda *_args, **_kwargs: _FakeJwksClient(),
    )
    monkeypatch.setattr(app_module.jwt, "decode", _decode_without_jti)

    assert await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request) is None


@pytest.mark.asyncio
async def test_resolve_user_from_jwt_token_retries_jwks_lookup_after_key_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    settings = SimpleNamespace(
        hydra_jwks_url="http://hydra:4444/.well-known/jwks.json",
        hydra_issuer="http://hydra:4444/",
        hydra_jwks_cache_ttl_seconds=300.0,
        hydra_expected_audience=None,
        oauth_admin_client_id="solution2-admin",
        oauth_user1_client_id="solution2-user1",
        oauth_user2_client_id="solution2-user2",
        oauth_admin_tier=SubscriptionTier.ENTERPRISE,
        oauth_user1_tier=SubscriptionTier.PRO,
        oauth_user2_tier=SubscriptionTier.FREE,
        admin_api_key=DEFAULT_ADMIN_API_KEY,
        alice_api_key=DEFAULT_USER1_API_KEY,
        bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
        oauth_admin_user_id=UUID("5ba7f2f8-24be-448a-9552-3af6e06e8898"),
        oauth_user1_user_id=TEST_USER_ID,
        oauth_user2_user_id=ALT_USER_ID,
    )

    app = FastAPI()

    async def _not_revoked(_: str, __: str) -> int:
        return 0

    app.state.runtime = SimpleNamespace(
        settings=settings,
        redis_client=SimpleNamespace(sismember=_not_revoked),
    )
    request = _request_for(app)

    class _FakeSigningKey:
        key = "fake-public-key"

    class _FakeJwksClient:
        def __init__(self) -> None:
            self.attempts = 0

        def get_signing_key_from_jwt(self, _: str) -> _FakeSigningKey:
            self.attempts += 1
            if self.attempts == 1:
                raise app_module.jwt.PyJWKError("stale key id")
            return _FakeSigningKey()

    fake_client = _FakeJwksClient()
    refreshes: list[bool] = []

    def _decode(*_: object, **__: object) -> dict[str, object]:
        return {
            "client_id": "solution2-user1",
            "sub": "solution2-user1",
            "jti": "rotate-jwt",
            "role": UserRole.USER.value,
            "tier": SubscriptionTier.PRO.value,
        }

    def _fake_jwks_client(*_args: object, **kwargs: object) -> _FakeJwksClient:
        refreshes.append(cast(bool, kwargs["force_refresh"]))
        return fake_client

    monkeypatch.setattr(app_module, "_jwks_client", _fake_jwks_client)
    monkeypatch.setattr(app_module.jwt, "decode", _decode)

    resolved = await app_module.resolve_user_from_jwt_token(token="a.b.c", request=request)
    assert resolved is not None
    assert resolved.user_id == TEST_USER_ID
    assert refreshes == [False, True]


def test_require_scopes_enforces_missing_scope() -> None:
    app_module = _load_app_module()
    user = AuthUser(
        api_key="key",
        user_id=TEST_USER_ID,
        name=TEST_USER_NAME,
        role=UserRole.USER,
        credits=0,
        scopes=frozenset({"task:poll"}),
    )

    with pytest.raises(app_module.HTTPException) as missing_scope_exc:
        app_module._require_scopes(current_user=user, required_scopes=frozenset({"task:submit"}))
    assert missing_scope_exc.value.status_code == 403

    # No exception when all required scopes are present.
    app_module._require_scopes(current_user=user, required_scopes=frozenset({"task:poll"}))


def test_oauth_client_credential_resolution_branches() -> None:
    app_module = _load_app_module()
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(
            admin_api_key=DEFAULT_ADMIN_API_KEY,
            alice_api_key=DEFAULT_USER1_API_KEY,
            bob_api_key="c9169bc2-2980-4155-be29-442ffc44ce64",
            oauth_admin_client_id="solution2-admin",
            oauth_admin_client_secret="solution2-admin-secret",
            oauth_user1_client_id="solution2-user1",
            oauth_user1_client_secret="solution2-user1-secret",
            oauth_user2_client_id="solution2-user2",
            oauth_user2_client_secret="solution2-user2-secret",
        )
    )
    request = _request_for(app)

    admin_payload = OAuthTokenRequest(api_key=DEFAULT_ADMIN_API_KEY)
    client_id, client_secret = app_module._resolve_oauth_client_credentials(
        payload=admin_payload, request=request
    )
    assert client_id == "solution2-admin"
    assert client_secret == "solution2-admin-secret"

    direct_payload = OAuthTokenRequest(
        client_id="direct-client",
        client_secret="direct-secret",
    )
    direct_id, direct_secret = app_module._resolve_oauth_client_credentials(
        payload=direct_payload, request=request
    )
    assert direct_id == "direct-client"
    assert direct_secret == "direct-secret"

    with pytest.raises(ValueError):
        app_module._resolve_oauth_client_credentials(
            payload=OAuthTokenRequest(api_key="00000000-0000-0000-0000-000000000000"),
            request=request,
        )

    with pytest.raises(ValueError):
        app_module._resolve_oauth_client_credentials(
            payload=cast(Any, SimpleNamespace(api_key=None, client_id=None, client_secret=None)),
            request=request,
        )


@pytest.mark.asyncio
async def test_exchange_client_credentials_and_validation_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = _load_app_module()
    app = FastAPI()
    app.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(
            hydra_public_url="http://hydra:4444",
            oauth_request_timeout_seconds=1.0,
        ),
        db_pool=SimpleNamespace(),
    )
    request = _request_for(app)

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _FakeClient:
        def __init__(self, *, response: _FakeResponse | None = None, fail: bool = False) -> None:
            self._response = response
            self._fail = fail

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(self, *_: object, **__: object) -> _FakeResponse:
            if self._fail:
                raise app_module.httpx.HTTPError("network")
            assert self._response is not None
            return self._response

    monkeypatch.setattr(
        app_module.httpx,
        "AsyncClient",
        lambda **_: _FakeClient(fail=True),
    )
    with pytest.raises(app_module.HTTPException) as degraded_exc:
        await app_module._exchange_client_credentials_for_token(
            client_id="c",
            client_secret="s",
            scope="task:submit",
            request=request,
        )
    assert degraded_exc.value.status_code == 503

    for status_code, expected in ((400, 401), (500, 503), (418, 503)):
        monkeypatch.setattr(
            app_module.httpx,
            "AsyncClient",
            lambda status_code=status_code, **_: _FakeClient(
                response=_FakeResponse(status_code=status_code, payload={}),
                fail=False,
            ),
        )
        with pytest.raises(app_module.HTTPException) as response_exc:
            await app_module._exchange_client_credentials_for_token(
                client_id="c",
                client_secret="s",
                scope="task:submit",
                request=request,
            )
        assert response_exc.value.status_code == expected

    monkeypatch.setattr(
        app_module.httpx,
        "AsyncClient",
        lambda **_: _FakeClient(
            response=_FakeResponse(
                status_code=200,
                payload={
                    "access_token": "token",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "scope": "task:submit",
                },
            ),
            fail=False,
        ),
    )
    token_payload = await app_module._exchange_client_credentials_for_token(
        client_id="c",
        client_secret="s",
        scope="task:submit",
        request=request,
    )
    assert token_payload["access_token"] == "token"

    async def _is_active(_: object, __: str) -> bool:
        return True

    monkeypatch.setattr(app_module, "is_active_api_key_hash", _is_active)
    assert (
        await app_module._validate_oauth_api_key(api_key=DEFAULT_USER1_API_KEY, request=request)
        is True
    )


def test_middleware_exception_and_http_error_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = _load_app_module()
    app = app_module.create_app()

    @asynccontextmanager
    async def _noop_lifespan(_: Any) -> Any:
        yield

    app.router.lifespan_context = _noop_lifespan

    async def _boom() -> dict[str, str]:
        raise RuntimeError("boom")

    async def _err503() -> dict[str, str]:
        raise app_module.HTTPException(status_code=503, detail="down")

    async def _err404() -> dict[str, str]:
        raise app_module.HTTPException(status_code=404, detail="missing")

    async def _err409() -> dict[str, str]:
        raise app_module.HTTPException(status_code=409, detail="conflict")

    async def _err403() -> dict[str, str]:
        raise app_module.HTTPException(status_code=403, detail="forbidden")

    async def _err418() -> dict[str, str]:
        raise app_module.HTTPException(status_code=418, detail="teapot")

    app.add_api_route("/boom", _boom, methods=["GET"])
    app.add_api_route("/err503", _err503, methods=["GET"])
    app.add_api_route("/err404", _err404, methods=["GET"])
    app.add_api_route("/err409", _err409, methods=["GET"])
    app.add_api_route("/err403", _err403, methods=["GET"])
    app.add_api_route("/err418", _err418, methods=["GET"])

    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 500
        assert client.get("/err503").json()["error"]["code"] == "SERVICE_DEGRADED"
        assert client.get("/err404").json()["error"]["code"] == "NOT_FOUND"
        assert client.get("/err409").json()["error"]["code"] == "CONFLICT"
        assert client.get("/err403").json()["error"]["code"] == "FORBIDDEN"
        assert client.get("/err418").json()["error"]["code"] == "HTTP_ERROR"
        validation = client.post("/v1/oauth/token", json={"client_id": "only"})
        assert validation.status_code == 400
        assert validation.json()["error"]["code"] == "BAD_REQUEST"
