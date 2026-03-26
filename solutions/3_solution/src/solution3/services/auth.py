from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, cast

import httpx
import jwt
from fastapi import HTTPException, Request

from solution3.constants import SubscriptionTier, UserRole
from solution3.core.runtime import RuntimeState
from solution3.db.repository import is_active_api_key_hash
from solution3.models.domain import AuthUser
from solution3.models.schemas import OAuthTokenRequest

_JWKS_CACHE: dict[str, tuple[jwt.PyJWKClient, float]] = {}
_JWKS_CACHE_LOCK = threading.Lock()


def parse_bearer_token(raw_authorization: str | None) -> str | None:
    if raw_authorization is None:
        return None
    parts = raw_authorization.strip().split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token_value = token.strip()
    return token_value if token_value else None


def runtime_state_from_request(request: Request) -> RuntimeState:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("runtime state is not initialized")
    return cast(RuntimeState, runtime)


def _parse_scope_claim(raw_scope: object) -> frozenset[str]:
    if isinstance(raw_scope, str):
        return frozenset(token for token in raw_scope.replace(",", " ").split() if token)
    if isinstance(raw_scope, list):
        values: list[str] = []
        for item in raw_scope:
            if isinstance(item, str):
                values.extend(token for token in item.replace(",", " ").split() if token)
        return frozenset(values)
    return frozenset()


def _oauth_principal_for_client(*, client_id: str, runtime: RuntimeState) -> AuthUser | None:
    settings = runtime.settings
    if client_id == settings.oauth_admin_client_id:
        return AuthUser(
            api_key=settings.admin_api_key,
            user_id=settings.oauth_admin_user_id,
            name=settings.oauth_admin_client_id,
            role=UserRole.ADMIN,
            tier=SubscriptionTier(settings.oauth_admin_tier),
            scopes=frozenset(),
        )
    if client_id == settings.oauth_user1_client_id:
        return AuthUser(
            api_key=settings.alice_api_key,
            user_id=settings.oauth_user1_user_id,
            name=settings.oauth_user1_client_id,
            role=UserRole.USER,
            tier=SubscriptionTier(settings.oauth_user1_tier),
            scopes=frozenset(),
        )
    if client_id == settings.oauth_user2_client_id:
        return AuthUser(
            api_key=settings.bob_api_key,
            user_id=settings.oauth_user2_user_id,
            name=settings.oauth_user2_client_id,
            role=UserRole.USER,
            tier=SubscriptionTier(settings.oauth_user2_tier),
            scopes=frozenset(),
        )
    return None


def _jwks_client(jwks_url: str, *, cache_ttl_seconds: float) -> jwt.PyJWKClient:
    now = time.monotonic()
    with _JWKS_CACHE_LOCK:
        cached = _JWKS_CACHE.get(jwks_url)
        if cached is not None and cache_ttl_seconds > 0:
            client, loaded_at = cached
            if now - loaded_at <= cache_ttl_seconds:
                return client
        client = jwt.PyJWKClient(jwks_url)
        _JWKS_CACHE[jwks_url] = (client, now)
        return client


async def _decode_token(*, token: str, runtime: RuntimeState) -> dict[str, Any]:
    jwks_client = _jwks_client(
        runtime.settings.hydra_jwks_url,
        cache_ttl_seconds=runtime.settings.oauth_jwks_cache_ttl_seconds,
    )
    signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)
    return await asyncio.to_thread(
        jwt.decode,
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=runtime.settings.hydra_issuer,
        options={"verify_aud": False},
    )


async def resolve_user_from_jwt_token(*, token: str, request: Request) -> AuthUser | None:
    runtime = runtime_state_from_request(request)
    try:
        claims = await _decode_token(token=token, runtime=runtime)
    except jwt.PyJWTError:
        return None

    client_id = claims.get("client_id") or claims.get("sub")
    if not isinstance(client_id, str) or not client_id:
        return None

    principal = _oauth_principal_for_client(client_id=client_id, runtime=runtime)
    if principal is None:
        return None

    scopes = _parse_scope_claim(claims.get("scope")) | _parse_scope_claim(claims.get("scp"))
    return AuthUser(
        api_key=principal.api_key,
        user_id=principal.user_id,
        name=principal.name,
        role=principal.role,
        tier=principal.tier,
        scopes=scopes,
    )


async def require_authenticated_user(request: Request) -> AuthUser:
    token = parse_bearer_token(request.headers.get("Authorization"))
    if token is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    user = await resolve_user_from_jwt_token(token=token, request=request)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return user


def require_scopes(*, current_user: AuthUser, required_scopes: frozenset[str]) -> None:
    missing = required_scopes - current_user.scopes
    if missing:
        missing_scope = sorted(missing)[0]
        raise HTTPException(status_code=403, detail=f"Missing required scope: {missing_scope}")


async def require_admin_user(request: Request) -> AuthUser:
    user = await require_authenticated_user(request)
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def resolve_oauth_client_credentials(
    *, payload: OAuthTokenRequest, runtime: RuntimeState
) -> tuple[str, str]:
    settings = runtime.settings
    if payload.api_key:
        if payload.api_key == settings.admin_api_key:
            return settings.oauth_admin_client_id, settings.oauth_admin_client_secret
        if payload.api_key == settings.alice_api_key:
            return settings.oauth_user1_client_id, settings.oauth_user1_client_secret
        if payload.api_key == settings.bob_api_key:
            return settings.oauth_user2_client_id, settings.oauth_user2_client_secret
        raise HTTPException(status_code=401, detail="Invalid OAuth credentials")
    if payload.client_id is None or payload.client_secret is None:
        raise HTTPException(status_code=401, detail="Invalid OAuth credentials")
    return payload.client_id, payload.client_secret


async def validate_oauth_api_key(*, api_key: str, request: Request) -> bool:
    runtime = runtime_state_from_request(request)
    db_pool = runtime.db_pool
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Authentication backend unavailable")
    return await is_active_api_key_hash(db_pool, api_key)


async def exchange_client_credentials_for_token(
    *,
    client_id: str,
    client_secret: str,
    scope: str,
    request: Request,
) -> dict[str, Any]:
    runtime = runtime_state_from_request(request)
    token_endpoint = f"{str(runtime.settings.hydra_public_url).rstrip('/')}/oauth2/token"
    try:
        async with httpx.AsyncClient(
            timeout=runtime.settings.oauth_request_timeout_seconds
        ) as client:
            response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": scope,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="OAuth backend unavailable") from exc

    if response.status_code in {400, 401}:
        raise HTTPException(status_code=401, detail="Invalid OAuth credentials")
    if response.status_code >= 500 or response.status_code != 200:
        raise HTTPException(status_code=503, detail="OAuth backend unavailable")
    body = cast(dict[str, Any], response.json())
    return {
        "access_token": str(body["access_token"]),
        "token_type": str(body.get("token_type", "bearer")),
        "expires_in": int(body.get("expires_in", 0)),
        "scope": str(body["scope"]) if body.get("scope") is not None else None,
    }
