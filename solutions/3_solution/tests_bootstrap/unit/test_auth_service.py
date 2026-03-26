from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import jwt
import pytest
from fastapi import HTTPException, Request

from solution3.constants import SubscriptionTier, UserRole
from solution3.core.runtime import RuntimeState
from solution3.models.domain import AuthUser
from solution3.models.schemas import OAuthTokenRequest
from solution3.services import auth


def _runtime(*, db_pool: object | None = object()) -> RuntimeState:
    settings = SimpleNamespace(
        hydra_jwks_url="http://hydra/jwks.json",
        oauth_jwks_cache_ttl_seconds=60.0,
        hydra_issuer="http://hydra",
        oauth_admin_client_id="admin-client",
        oauth_admin_client_secret="admin-secret",
        oauth_admin_user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        oauth_admin_tier="enterprise",
        oauth_user1_client_id="user1-client",
        oauth_user1_client_secret="user1-secret",
        oauth_user1_user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        oauth_user1_tier="pro",
        oauth_user2_client_id="user2-client",
        oauth_user2_client_secret="user2-secret",
        oauth_user2_user_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        oauth_user2_tier="free",
        admin_api_key="admin-api-key-value-0000000000000000",
        alice_api_key="user1-api-key-value-0000000000000000",
        bob_api_key="user2-api-key-value-0000000000000000",
        hydra_public_url="http://hydra-public",
        oauth_request_timeout_seconds=5.0,
    )
    return RuntimeState(
        settings=cast(Any, settings),
        db_pool=cast(Any, db_pool),
        redis_client=None,
        billing_client=None,
        started=True,
    )


def _request(*, runtime: RuntimeState, authorization: str | None = None) -> Request:
    headers: dict[str, str] = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)),
        headers=headers,
    )
    return cast(Request, request)


def test_parse_bearer_token_accepts_valid_header() -> None:
    assert auth.parse_bearer_token("Bearer token-123") == "token-123"
    assert auth.parse_bearer_token(" bearer  token-456 ") == "token-456"
    assert auth.parse_bearer_token(None) is None
    assert auth.parse_bearer_token("Basic abc") is None
    assert auth.parse_bearer_token("Bearer   ") is None


def test_runtime_state_from_request_requires_initialized_runtime() -> None:
    request = cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))

    with pytest.raises(RuntimeError, match="runtime state is not initialized"):
        auth.runtime_state_from_request(request)


def test_parse_scope_claim_accepts_multiple_shapes() -> None:
    assert auth._parse_scope_claim("tasks.read tasks.write") == frozenset(
        {"tasks.read", "tasks.write"}
    )
    assert auth._parse_scope_claim(["tasks.read,tasks.write", "admin"]) == frozenset(
        {"tasks.read", "tasks.write", "admin"}
    )
    assert auth._parse_scope_claim({"scope": "ignored"}) == frozenset()


def test_oauth_principal_for_client_maps_seed_clients() -> None:
    runtime = _runtime()

    admin = auth._oauth_principal_for_client(client_id="admin-client", runtime=runtime)
    user1 = auth._oauth_principal_for_client(client_id="user1-client", runtime=runtime)
    user2 = auth._oauth_principal_for_client(client_id="user2-client", runtime=runtime)
    missing = auth._oauth_principal_for_client(client_id="unknown", runtime=runtime)

    assert admin is not None
    assert admin.role == UserRole.ADMIN
    assert admin.tier == SubscriptionTier.ENTERPRISE
    assert user1 is not None
    assert user1.role == UserRole.USER
    assert user1.tier == SubscriptionTier.PRO
    assert user2 is not None
    assert user2.role == UserRole.USER
    assert user2.tier == SubscriptionTier.FREE
    assert missing is None


def test_jwks_client_caches_until_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    created_urls: list[str] = []
    monotonic_values = iter([10.0, 20.0, 95.0])

    class FakeJWKClient:
        def __init__(self, url: str) -> None:
            created_urls.append(url)

    auth._JWKS_CACHE.clear()
    monkeypatch.setattr("solution3.services.auth.jwt.PyJWKClient", FakeJWKClient)
    monkeypatch.setattr(
        "solution3.services.auth.time.monotonic",
        lambda: next(monotonic_values),
    )

    first = auth._jwks_client("http://hydra/jwks", cache_ttl_seconds=60.0)
    second = auth._jwks_client("http://hydra/jwks", cache_ttl_seconds=60.0)
    third = auth._jwks_client("http://hydra/jwks", cache_ttl_seconds=60.0)

    assert first is second
    assert third is not first
    assert created_urls == ["http://hydra/jwks", "http://hydra/jwks"]


@pytest.mark.asyncio
async def test_resolve_user_from_jwt_token_returns_scoped_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(runtime=runtime)

    async def fake_decode_token(*, token: str, runtime: RuntimeState) -> dict[str, Any]:
        assert token == "jwt-token"
        assert runtime is request.app.state.runtime
        return {
            "client_id": "user1-client",
            "scope": "tasks.read tasks.write",
            "scp": ["admin.read,credits.write"],
        }

    monkeypatch.setattr(auth, "_decode_token", fake_decode_token)

    resolved = await auth.resolve_user_from_jwt_token(token="jwt-token", request=request)

    assert resolved == AuthUser(
        api_key=runtime.settings.alice_api_key,
        user_id=runtime.settings.oauth_user1_user_id,
        name="user1-client",
        role=UserRole.USER,
        tier=SubscriptionTier.PRO,
        scopes=frozenset({"tasks.read", "tasks.write", "admin.read", "credits.write"}),
    )


@pytest.mark.asyncio
async def test_resolve_user_from_jwt_token_rejects_invalid_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(runtime=runtime)

    async def fake_decode_token(*, token: str, runtime: RuntimeState) -> dict[str, Any]:
        _ = (token, runtime)
        raise jwt.PyJWTError("bad token")

    monkeypatch.setattr(auth, "_decode_token", fake_decode_token)

    assert await auth.resolve_user_from_jwt_token(token="jwt-token", request=request) is None


@pytest.mark.asyncio
async def test_resolve_user_from_jwt_token_rejects_missing_or_unknown_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(runtime=runtime)

    async def fake_decode_missing(*, token: str, runtime: RuntimeState) -> dict[str, Any]:
        _ = (token, runtime)
        return {"scope": "tasks.read"}

    monkeypatch.setattr(auth, "_decode_token", fake_decode_missing)
    assert await auth.resolve_user_from_jwt_token(token="jwt-token", request=request) is None

    async def fake_decode_unknown(*, token: str, runtime: RuntimeState) -> dict[str, Any]:
        _ = (token, runtime)
        return {"client_id": "unknown-client"}

    monkeypatch.setattr(auth, "_decode_token", fake_decode_unknown)
    assert await auth.resolve_user_from_jwt_token(token="jwt-token", request=request) is None


@pytest.mark.asyncio
async def test_decode_token_uses_cached_jwks_client_and_jwt_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    jwks_calls: list[str] = []
    decode_calls: list[tuple[str, object, list[str], str, dict[str, bool]]] = []

    class FakeSigningKey:
        def __init__(self, key: str) -> None:
            self.key = key

    class FakeJWKClient:
        def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
            jwks_calls.append(token)
            return FakeSigningKey("public-key")

    def fake_jwks_client(jwks_url: str, *, cache_ttl_seconds: float) -> FakeJWKClient:
        assert jwks_url == runtime.settings.hydra_jwks_url
        assert cache_ttl_seconds == runtime.settings.oauth_jwks_cache_ttl_seconds
        return FakeJWKClient()

    def fake_decode(
        token: str,
        key: object,
        *,
        algorithms: list[str],
        issuer: str,
        options: dict[str, bool],
    ) -> dict[str, Any]:
        decode_calls.append((token, key, algorithms, issuer, options))
        return {"sub": "client-a"}

    monkeypatch.setattr(auth, "_jwks_client", fake_jwks_client)
    monkeypatch.setattr("solution3.services.auth.jwt.decode", fake_decode)

    claims = await auth._decode_token(token="jwt-token", runtime=runtime)

    assert claims == {"sub": "client-a"}
    assert jwks_calls == ["jwt-token"]
    assert decode_calls == [
        (
            "jwt-token",
            "public-key",
            ["RS256"],
            runtime.settings.hydra_issuer,
            {"verify_aud": False},
        )
    ]


@pytest.mark.asyncio
async def test_require_authenticated_and_admin_user_enforce_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    admin_request = _request(runtime=runtime, authorization="Bearer good")
    user_request = _request(runtime=runtime, authorization="Bearer good")

    async def fake_resolve_user(*, token: str, request: Request) -> AuthUser | None:
        if request is admin_request:
            return AuthUser(
                api_key="admin-key",
                user_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                name="admin",
                role=UserRole.ADMIN,
                tier=SubscriptionTier.ENTERPRISE,
                scopes=frozenset(),
            )
        return AuthUser(
            api_key="user-key",
            user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            name="user",
            role=UserRole.USER,
            tier=SubscriptionTier.PRO,
            scopes=frozenset(),
        )

    monkeypatch.setattr(auth, "resolve_user_from_jwt_token", fake_resolve_user)

    assert (await auth.require_authenticated_user(admin_request)).role == UserRole.ADMIN
    assert (await auth.require_admin_user(admin_request)).role == UserRole.ADMIN

    with pytest.raises(HTTPException, match="Admin role required"):
        await auth.require_admin_user(user_request)

    with pytest.raises(HTTPException, match="Missing bearer token"):
        await auth.require_authenticated_user(_request(runtime=runtime))

    async def fake_invalid_user(*, token: str, request: Request) -> AuthUser | None:
        _ = (token, request)
        return None

    monkeypatch.setattr(auth, "resolve_user_from_jwt_token", fake_invalid_user)

    with pytest.raises(HTTPException, match="Invalid bearer token"):
        await auth.require_authenticated_user(_request(runtime=runtime, authorization="Bearer bad"))


def test_require_scopes_reports_first_missing_scope() -> None:
    current_user = AuthUser(
        api_key="user-key",
        user_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        name="user",
        role=UserRole.USER,
        tier=SubscriptionTier.PRO,
        scopes=frozenset({"tasks.read"}),
    )

    with pytest.raises(HTTPException, match=r"Missing required scope: credits.write"):
        auth.require_scopes(
            current_user=current_user,
            required_scopes=frozenset({"tasks.read", "credits.write"}),
        )


def test_resolve_oauth_client_credentials_accepts_api_key_and_client_secret_forms() -> None:
    runtime = _runtime()
    admin_payload = OAuthTokenRequest(api_key=runtime.settings.admin_api_key)
    api_key_payload = OAuthTokenRequest(api_key=runtime.settings.alice_api_key)
    user2_payload = OAuthTokenRequest(api_key=runtime.settings.bob_api_key)
    client_payload = OAuthTokenRequest(client_id="client-a", client_secret="secret-a")

    assert auth.resolve_oauth_client_credentials(payload=admin_payload, runtime=runtime) == (
        runtime.settings.oauth_admin_client_id,
        runtime.settings.oauth_admin_client_secret,
    )
    assert auth.resolve_oauth_client_credentials(payload=api_key_payload, runtime=runtime) == (
        runtime.settings.oauth_user1_client_id,
        runtime.settings.oauth_user1_client_secret,
    )
    assert auth.resolve_oauth_client_credentials(payload=user2_payload, runtime=runtime) == (
        runtime.settings.oauth_user2_client_id,
        runtime.settings.oauth_user2_client_secret,
    )
    assert auth.resolve_oauth_client_credentials(payload=client_payload, runtime=runtime) == (
        "client-a",
        "secret-a",
    )

    with pytest.raises(HTTPException, match="Invalid OAuth credentials"):
        auth.resolve_oauth_client_credentials(
            payload=OAuthTokenRequest(api_key="invalid-api-key-000000000000000000"),
            runtime=runtime,
        )

    with pytest.raises(HTTPException, match="Invalid OAuth credentials"):
        auth.resolve_oauth_client_credentials(
            payload=cast(
                Any, SimpleNamespace(api_key=None, client_id="client-a", client_secret=None)
            ),
            runtime=runtime,
        )


@pytest.mark.asyncio
async def test_validate_oauth_api_key_requires_backend_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(runtime=runtime)
    validated_keys: list[str] = []

    async def fake_is_active_api_key_hash(pool: object, api_key: str) -> bool:
        assert pool is runtime.db_pool
        validated_keys.append(api_key)
        return True

    monkeypatch.setattr(auth, "is_active_api_key_hash", fake_is_active_api_key_hash)

    assert await auth.validate_oauth_api_key(api_key="key-1", request=request) is True
    assert validated_keys == ["key-1"]

    with pytest.raises(HTTPException, match="Authentication backend unavailable"):
        await auth.validate_oauth_api_key(
            api_key="key-2",
            request=_request(runtime=_runtime(db_pool=None)),
        )


@pytest.mark.asyncio
async def test_exchange_client_credentials_for_token_maps_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    request = _request(runtime=runtime)
    requested_payloads: list[dict[str, str]] = []

    class FakeResponse:
        def __init__(self, status_code: int, body: dict[str, Any]) -> None:
            self.status_code = status_code
            self._body = body

        def json(self) -> dict[str, Any]:
            return self._body

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == runtime.settings.oauth_request_timeout_seconds

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            _ = (exc_type, exc, tb)

        async def post(
            self,
            url: str,
            *,
            data: dict[str, str],
            headers: dict[str, str],
        ) -> FakeResponse:
            requested_payloads.append(data)
            assert url == "http://hydra-public/oauth2/token"
            assert headers == {"Content-Type": "application/x-www-form-urlencoded"}
            return FakeResponse(
                200,
                {
                    "access_token": "access-token",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "scope": "tasks.read",
                },
            )

    monkeypatch.setattr("solution3.services.auth.httpx.AsyncClient", FakeAsyncClient)

    response = await auth.exchange_client_credentials_for_token(
        client_id="client-a",
        client_secret="secret-a",
        scope="tasks.read",
        request=request,
    )

    assert response == {
        "access_token": "access-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "scope": "tasks.read",
    }
    assert requested_payloads == [
        {
            "grant_type": "client_credentials",
            "client_id": "client-a",
            "client_secret": "secret-a",
            "scope": "tasks.read",
        }
    ]

    class FailingAsyncClient(FakeAsyncClient):
        async def post(
            self,
            url: str,
            *,
            data: dict[str, str],
            headers: dict[str, str],
        ) -> FakeResponse:
            _ = (url, data, headers)
            return FakeResponse(401, {})

    monkeypatch.setattr("solution3.services.auth.httpx.AsyncClient", FailingAsyncClient)

    with pytest.raises(HTTPException, match="Invalid OAuth credentials"):
        await auth.exchange_client_credentials_for_token(
            client_id="client-a",
            client_secret="secret-a",
            scope="tasks.read",
            request=request,
        )

    class BackendFailureClient(FakeAsyncClient):
        async def post(
            self,
            url: str,
            *,
            data: dict[str, str],
            headers: dict[str, str],
        ) -> FakeResponse:
            _ = (url, data, headers)
            return FakeResponse(503, {})

    class TransportFailureClient(FakeAsyncClient):
        async def post(
            self,
            url: str,
            *,
            data: dict[str, str],
            headers: dict[str, str],
        ) -> FakeResponse:
            _ = (url, data, headers)
            raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(
        "solution3.services.auth.httpx.AsyncClient",
        BackendFailureClient,
    )

    with pytest.raises(HTTPException, match="OAuth backend unavailable"):
        await auth.exchange_client_credentials_for_token(
            client_id="client-a",
            client_secret="secret-a",
            scope="tasks.read",
            request=request,
        )

    monkeypatch.setattr(
        "solution3.services.auth.httpx.AsyncClient",
        TransportFailureClient,
    )

    with pytest.raises(HTTPException, match="OAuth backend unavailable"):
        await auth.exchange_client_credentials_for_token(
            client_id="client-a",
            client_secret="secret-a",
            scope="tasks.read",
            request=request,
        )
