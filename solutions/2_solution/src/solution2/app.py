"""FastAPI application assembly and shared runtime helpers for Solution 2."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import asyncpg
import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind
from redis.asyncio import Redis

from solution2.api.admin_routes import register_admin_routes
from solution2.api.contracts import (
    AdminRoutesApp,
    SystemRoutesApp,
    TaskReadRoutesApp,
    TaskWriteRoutesApp,
    WebhookRoutesApp,
)
from solution2.api.paths import V1_AUTH_REVOKE_PATH, V1_OAUTH_TOKEN_PATH
from solution2.api.system_routes import register_system_routes
from solution2.api.task_read_routes import register_task_read_routes
from solution2.api.task_write_routes import register_task_write_routes
from solution2.api.webhook_routes import register_webhook_routes
from solution2.constants import (
    ADMIN_ROLE,
    DEFAULT_TASK_STATUS,
    TASK_CANCELLABLE_STATUSES,
    TASK_RUNNING_STATUSES,
    TASK_TERMINAL_STATUSES,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)
from solution2.core.dependencies import DependencyHealthService, build_dependency_health_service
from solution2.core.runtime import RuntimeState
from solution2.core.settings import AppSettings, load_settings
from solution2.db.migrate import run_migrations
from solution2.db.repository import (
    add_user_credits,
    admin_update_user_credits,
    create_outbox_event,
    disable_webhook_subscription,
    get_credit_reservation,
    get_task_command,
    get_task_query_view,
    get_webhook_subscription,
    insert_credit_transaction,
    is_active_api_key_hash,
    is_jti_revoked,
    load_active_revoked_jtis,
    release_reservation,
    update_task_command_cancelled,
    upsert_webhook_subscription,
)
from solution2.models.domain import AuthUser
from solution2.models.schemas import (
    AdminCreditsRequest,
    AdminCreditsResponse,
    CancelTaskResponse,
    ErrorEnvelope,
    ErrorPayload,
    OAuthTokenRequest,
    OAuthTokenResponse,
    PollTaskResponse,
    RevokeTokenResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
    WebhookConfigRequest,
    WebhookConfigResponse,
)
from solution2.observability.metrics import (
    CREDIT_DEDUCTIONS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    JWT_VALIDATION_DURATION_SECONDS,
    REVOCATION_CHECK_DURATION_SECONDS,
    REVOCATION_PG_FALLBACK_TOTAL,
    STREAM_QUEUE_DEPTH,
    TASK_SUBMISSIONS_TOTAL,
    TOKEN_ISSUANCE_TOTAL,
    TOKEN_REVOCATIONS_TOTAL,
)
from solution2.observability.tracing import configure_process_tracing, start_span
from solution2.services.auth import (
    invalidate_user_auth_cache,
    parse_bearer_token,
    resolve_user_from_api_key,
    revoke_jti,
    revoked_tokens_day_key,
    revoked_tokens_lookup_keys,
    task_state_key,
)
from solution2.services.billing import (
    run_admission_gate,
    run_batch_admission_gate,
    run_sync_submission,
)
from solution2.utils.logging import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)

logger = get_logger("solution2.api")

# Route modules receive this module object and resolve these symbols dynamically.
# Tests monkeypatch these names directly on `solution2.app`.
_ROUTE_MODULE_EXPORTS: tuple[object, ...] = (
    ADMIN_ROLE,
    DEFAULT_TASK_STATUS,
    TASK_CANCELLABLE_STATUSES,
    TASK_RUNNING_STATUSES,
    TASK_TERMINAL_STATUSES,
    TaskStatus,
    AdminCreditsRequest,
    AdminCreditsResponse,
    CancelTaskResponse,
    PollTaskResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
    WebhookConfigRequest,
    WebhookConfigResponse,
    STREAM_QUEUE_DEPTH,
    CREDIT_DEDUCTIONS_TOTAL,
    TASK_SUBMISSIONS_TOTAL,
    admin_update_user_credits,
    add_user_credits,
    create_outbox_event,
    disable_webhook_subscription,
    get_credit_reservation,
    get_task_command,
    get_task_query_view,
    get_webhook_subscription,
    insert_credit_transaction,
    upsert_webhook_subscription,
    invalidate_user_auth_cache,
    resolve_user_from_api_key,
    task_state_key,
    release_reservation,
    update_task_command_cancelled,
    run_admission_gate,
    run_batch_admission_gate,
    run_sync_submission,
)


class _TaskCancellationConflict(Exception):
    """Raised when cancel transition loses a status race."""

    pass


class _ExpiredBearerToken(Exception):
    """Raised when JWT verification fails due to expiration."""

    pass


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retry_after: int | None = None,
) -> JSONResponse:
    """Build the canonical JSON error envelope."""
    payload = ErrorEnvelope(error=ErrorPayload(code=code, message=message, retry_after=retry_after))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _runtime_state(request: Request) -> RuntimeState:
    """Fetch initialized runtime resources from app state."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("runtime state is not initialized")
    return cast(RuntimeState, runtime)


def _health_service(request: Request) -> DependencyHealthService:
    """Fetch dependency-health service from app state."""
    service = getattr(request.app.state, "dependency_health", None)
    if service is None:
        raise RuntimeError("dependency health service is not initialized")
    return cast(DependencyHealthService, service)


def _canonical_path_label(request: Request) -> str:
    """Return canonical route template when available to avoid high-cardinality labels."""
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return request.url.path


async def _authenticate(request: Request) -> AuthUser:
    """Resolve bearer token to authenticated user via JWT verification."""
    token = parse_bearer_token(request.headers.get("Authorization"))
    if token is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if token.count(".") != 2:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    try:
        jwt_user = await resolve_user_from_jwt_token(token=token, request=request)
    except _ExpiredBearerToken:
        raise HTTPException(status_code=401, detail="TOKEN_EXPIRED") from None
    except Exception as exc:
        logger.exception("jwt_auth_resolution_failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="Authentication backend unavailable",
        ) from exc
    if jwt_user is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return jwt_user


_JWKS_CACHE: dict[str, tuple[jwt.PyJWKClient, float]] = {}
_JWKS_CACHE_LOCK = threading.Lock()


def _jwks_client(
    jwks_url: str,
    *,
    cache_ttl_seconds: float,
    force_refresh: bool = False,
) -> jwt.PyJWKClient:
    now = time.monotonic()
    with _JWKS_CACHE_LOCK:
        cached = _JWKS_CACHE.get(jwks_url)
        if not force_refresh and cache_ttl_seconds > 0 and cached is not None:
            client, loaded_at = cached
            if now - loaded_at <= cache_ttl_seconds:
                return client

        client = jwt.PyJWKClient(jwks_url)
        _JWKS_CACHE[jwks_url] = (client, now)
        return client


def _oauth_principal_for_client(*, client_id: str, settings: AppSettings) -> AuthUser | None:
    if client_id == settings.oauth_admin_client_id:
        return AuthUser(
            api_key=settings.admin_api_key,
            user_id=settings.oauth_admin_user_id,
            name=settings.oauth_admin_client_id,
            role=UserRole.ADMIN,
            credits=0,
            tier=settings.oauth_admin_tier,
            scopes=frozenset(),
        )
    if client_id == settings.oauth_user1_client_id:
        return AuthUser(
            api_key=settings.alice_api_key,
            user_id=settings.oauth_user1_user_id,
            name=settings.oauth_user1_client_id,
            role=UserRole.USER,
            credits=0,
            tier=settings.oauth_user1_tier,
            scopes=frozenset(),
        )
    if client_id == settings.oauth_user2_client_id:
        return AuthUser(
            api_key=settings.bob_api_key,
            user_id=settings.oauth_user2_user_id,
            name=settings.oauth_user2_client_id,
            role=UserRole.USER,
            credits=0,
            tier=settings.oauth_user2_tier,
            scopes=frozenset(),
        )
    return None


def _decode_claims_sync(*, token: str, signing_key: Any, settings: AppSettings) -> dict[str, Any]:
    expected_audience = settings.hydra_expected_audience
    decode_kwargs: dict[str, object] = {
        "algorithms": ["RS256"],
        "options": {"verify_aud": bool(expected_audience)},
        "issuer": settings.hydra_issuer,
    }
    if isinstance(expected_audience, str) and expected_audience:
        decode_kwargs["audience"] = expected_audience
    return jwt.decode(
        token,
        signing_key.key,
        **cast(dict[str, Any], decode_kwargs),
    )


async def _decode_token_with_cached_jwks(
    *,
    token: str,
    settings: AppSettings,
    cache_ttl_seconds: float,
    force_refresh: bool = False,
) -> dict[str, Any]:
    jwks_client = _jwks_client(
        settings.hydra_jwks_url,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )
    signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)
    return await asyncio.to_thread(
        _decode_claims_sync,
        token=token,
        signing_key=signing_key,
        settings=settings,
    )


async def _load_jwt_claims(
    *, token: str, settings: AppSettings, cache_ttl_seconds: float
) -> tuple[dict[str, Any] | None, str]:
    try:
        claims = await _decode_token_with_cached_jwks(
            token=token,
            settings=settings,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        return claims, "ok"
    except jwt.PyJWKError:
        # Key rotation can invalidate a cached key id. Refresh JWKS and retry once.
        try:
            claims = await _decode_token_with_cached_jwks(
                token=token,
                settings=settings,
                cache_ttl_seconds=cache_ttl_seconds,
                force_refresh=True,
            )
        except jwt.PyJWKError:
            return None, "invalid"
        return claims, "ok"
    except jwt.ExpiredSignatureError as exc:
        raise _ExpiredBearerToken from exc
    except jwt.PyJWTError:
        return None, "invalid"
    except Exception as exc:
        logger.warning("jwt_verification_error", error=str(exc))
        return None, "error"


def _extract_client_id_from_claims(
    *, claims: dict[str, Any], observe: Callable[[str], None]
) -> str | None:
    client_id_claim = claims.get("client_id")
    sub_claim = claims.get("sub")
    if isinstance(client_id_claim, str) and client_id_claim:
        if isinstance(sub_claim, str) and sub_claim and sub_claim != client_id_claim:
            observe("invalid")
            return None
        return client_id_claim
    if isinstance(sub_claim, str) and sub_claim:
        return sub_claim
    observe("invalid")
    return None


def _authorize_jwt_claims(
    *,
    claims: dict[str, Any],
    principal: AuthUser,
    observe: Callable[[str], None],
) -> tuple[AuthUser | None, str | None]:
    role = principal.role
    role_claim = claims.get("role")
    if isinstance(role_claim, str):
        try:
            claim_role = UserRole(role_claim)
        except ValueError:
            observe("invalid")
            return None, None
        if claim_role != principal.role:
            observe("invalid")
            return None, None

    tier = principal.tier
    tier_claim = claims.get("tier")
    if isinstance(tier_claim, str):
        try:
            claim_tier = SubscriptionTier(tier_claim)
        except ValueError:
            observe("invalid")
            return None, None
        if claim_tier != principal.tier:
            observe("invalid")
            return None, None

    scopes = _parse_scope_claim(claims.get("scope")) | _parse_scope_claim(claims.get("scp"))
    jti = claims.get("jti")
    if not isinstance(jti, str):
        observe("invalid")
        return None, None
    jti = jti.strip()
    if not jti:
        observe("invalid")
        return None, None

    return (
        AuthUser(
            api_key=principal.api_key,
            user_id=principal.user_id,
            name=principal.name,
            role=role,
            credits=0,
            tier=tier,
            scopes=scopes,
        ),
        jti,
    )


def _jwt_expiry_from_claims(claims: dict[str, Any]) -> datetime | None:
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(exp), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


async def _is_token_revoked(*, runtime: RuntimeState, user_id: Any, jti: str) -> bool:
    revocation_keys = revoked_tokens_lookup_keys(user_id)
    redis_started_at = time.perf_counter()
    try:
        pipeline_factory = getattr(runtime.redis_client, "pipeline", None)
        if callable(pipeline_factory):
            async with pipeline_factory(transaction=False) as pipeline:
                if hasattr(pipeline, "sismember"):
                    for key in revocation_keys:
                        pipeline.sismember(key, jti)
                    revocation_results = await pipeline.execute()
                    revoked = any(bool(result) for result in revocation_results)
                    REVOCATION_CHECK_DURATION_SECONDS.labels(source="redis").observe(
                        time.perf_counter() - redis_started_at
                    )
                    return revoked
        checks = await asyncio.gather(
            *(runtime.redis_client.sismember(key, jti) for key in revocation_keys)
        )
        revoked = any(bool(result) for result in checks)
        REVOCATION_CHECK_DURATION_SECONDS.labels(source="redis").observe(
            time.perf_counter() - redis_started_at
        )
        return revoked
    except Exception as redis_exc:
        REVOCATION_PG_FALLBACK_TOTAL.inc()
        logger.warning("revocation_redis_check_failed", error=str(redis_exc))

    postgres_started_at = time.perf_counter()
    revoked = await is_jti_revoked(runtime.db_pool, jti=jti)
    REVOCATION_CHECK_DURATION_SECONDS.labels(source="postgres").observe(
        time.perf_counter() - postgres_started_at
    )
    if not revoked:
        return False

    try:
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        today_key = revoked_tokens_day_key(user_uuid, datetime.now(tz=UTC).date().isoformat())
        await runtime.redis_client.sadd(today_key, jti)
        await runtime.redis_client.expire(today_key, runtime.settings.revocation_bucket_ttl_seconds)
    except Exception as write_through_exc:
        logger.warning("revocation_write_through_failed", error=str(write_through_exc))

    return True


async def resolve_user_from_jwt_token(*, token: str, request: Request) -> AuthUser | None:
    """Verify JWT locally using Hydra JWKS and derive authenticated user context."""
    runtime = _runtime_state(request)
    settings = runtime.settings
    started_at = time.perf_counter()

    def _observe(result: str) -> None:
        JWT_VALIDATION_DURATION_SECONDS.labels(result=result).observe(
            time.perf_counter() - started_at
        )

    cache_ttl_seconds = settings.hydra_jwks_cache_ttl_seconds
    claims, result = await _load_jwt_claims(
        token=token, settings=settings, cache_ttl_seconds=cache_ttl_seconds
    )
    if claims is None:
        _observe(result)
        return None

    client_id = _extract_client_id_from_claims(claims=claims, observe=_observe)
    if client_id is None:
        return None

    principal = _oauth_principal_for_client(client_id=client_id, settings=settings)
    if principal is None:
        _observe("invalid")
        return None

    auth_user, jti = _authorize_jwt_claims(claims=claims, principal=principal, observe=_observe)
    if auth_user is None or jti is None:
        return None

    request.state.jwt_claims = dict(claims)
    request.state.jwt_client_id = client_id
    request.state.jwt_user_id = str(principal.user_id)
    request.state.jwt_jti = jti
    request.state.jwt_expiry = _jwt_expiry_from_claims(claims)

    is_revoked = await _is_token_revoked(
        runtime=runtime,
        user_id=principal.user_id,
        jti=jti,
    )
    if is_revoked:
        logger.info("jwt_revoked", client_id=client_id, user_id=str(principal.user_id), jti=jti)
        _observe("revoked")
        return None

    _observe("ok")
    return auth_user


def _parse_scope_claim(raw_scope: object) -> frozenset[str]:
    """Parse OAuth scope claim into a normalized scope set."""
    if isinstance(raw_scope, str):
        return frozenset(token for token in raw_scope.replace(",", " ").split() if token)
    if isinstance(raw_scope, list):
        values: list[str] = []
        for item in raw_scope:
            if isinstance(item, str):
                values.extend(token for token in item.replace(",", " ").split() if token)
        return frozenset(values)
    return frozenset()


def _require_scopes(*, current_user: AuthUser, required_scopes: frozenset[str]) -> None:
    """Reject requests that do not include all required OAuth scopes."""
    missing = required_scopes - current_user.scopes
    if missing:
        missing_scope = sorted(missing)[0]
        raise HTTPException(status_code=403, detail=f"Missing required scope: {missing_scope}")


def _resolve_oauth_client_credentials(
    *, payload: OAuthTokenRequest, request: Request
) -> tuple[str, str]:
    runtime = _runtime_state(request)
    settings = runtime.settings

    if payload.api_key:
        api_key = payload.api_key
        if api_key == settings.admin_api_key:
            return settings.oauth_admin_client_id, settings.oauth_admin_client_secret
        if api_key == settings.alice_api_key:
            return settings.oauth_user1_client_id, settings.oauth_user1_client_secret
        if api_key == settings.bob_api_key:
            return settings.oauth_user2_client_id, settings.oauth_user2_client_secret
        raise ValueError("Unsupported api_key for OAuth exchange")

    if payload.client_id and payload.client_secret:
        return payload.client_id, payload.client_secret
    raise ValueError("client_id and client_secret are required")


def _oauth_rate_limit_subject(payload: OAuthTokenRequest, request: Request) -> str:
    if payload.client_id:
        return f"client:{payload.client_id.strip()}"
    if payload.api_key:
        digest = hashlib.sha256(payload.api_key.encode("utf-8")).hexdigest()[:16]
        return f"api_key:{digest}"
    client_host = request.client.host if request.client is not None else "unknown"
    return f"ip:{client_host}"


async def _check_oauth_token_rate_limit(
    *, payload: OAuthTokenRequest, request: Request
) -> int | None:
    runtime = _runtime_state(request)
    settings = runtime.settings
    if not bool(getattr(settings, "oauth_token_rate_limit_enabled", True)):
        return None

    window_seconds = max(1, int(getattr(settings, "oauth_token_rate_limit_window_seconds", 60)))
    max_requests = max(1, int(getattr(settings, "oauth_token_rate_limit_max_requests", 120)))
    now_epoch = int(time.time())
    window_bucket = now_epoch // window_seconds
    subject = _oauth_rate_limit_subject(payload, request)
    key = f"ratelimit:oauth:{subject}:{window_bucket}"

    try:
        count = int(await runtime.redis_client.incr(key))
        if count == 1:
            await runtime.redis_client.expire(key, window_seconds + 1)
    except Exception as exc:
        logger.warning("oauth_rate_limit_check_failed", error=str(exc))
        return None

    if count <= max_requests:
        return None

    return max(1, window_seconds - (now_epoch % window_seconds))


async def _exchange_client_credentials_for_token(
    *,
    client_id: str,
    client_secret: str,
    scope: str,
    request: Request,
) -> dict[str, Any]:
    runtime = _runtime_state(request)
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
        logger.exception("oauth_token_exchange_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="OAuth backend unavailable") from exc

    if response.status_code in {400, 401}:
        raise HTTPException(status_code=401, detail="Invalid OAuth credentials")
    if response.status_code >= 500:
        raise HTTPException(status_code=503, detail="OAuth backend unavailable")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="OAuth backend returned unexpected response")

    body = cast(dict[str, Any], response.json())
    return {
        "access_token": str(body["access_token"]),
        "token_type": str(body.get("token_type", "bearer")),
        "expires_in": int(body.get("expires_in", 0)),
        "scope": str(body["scope"]) if body.get("scope") is not None else None,
    }


async def _validate_oauth_api_key(*, api_key: str, request: Request) -> bool:
    runtime = _runtime_state(request)
    return await is_active_api_key_hash(runtime.db_pool, api_key)


async def _rehydrate_revocation_cache(
    *,
    db_pool: asyncpg.Pool,
    redis_client: Redis[str],
    bucket_ttl_seconds: int,
) -> int:
    since = datetime.now(tz=UTC) - timedelta(days=1)
    revoked_entries = await load_active_revoked_jtis(db_pool, since=since)
    if not revoked_entries:
        logger.info("revocation_rehydrated", count=0)
        return 0

    pipeline_factory = getattr(redis_client, "pipeline", None)
    if callable(pipeline_factory):
        async with pipeline_factory(transaction=False) as pipeline:
            for jti, user_id, day_iso in revoked_entries:
                key = revoked_tokens_day_key(user_id, day_iso)
                if hasattr(pipeline, "sadd"):
                    pipeline.sadd(key, jti)
                if hasattr(pipeline, "expire"):
                    pipeline.expire(key, bucket_ttl_seconds)
            await pipeline.execute()
    else:
        for jti, user_id, day_iso in revoked_entries:
            key = revoked_tokens_day_key(user_id, day_iso)
            await redis_client.sadd(key, jti)
            await redis_client.expire(key, bucket_ttl_seconds)

    logger.info("revocation_rehydrated", count=len(revoked_entries))
    return len(revoked_entries)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and teardown external resources for the API process."""
    configure_logging()
    settings = load_settings()
    configure_process_tracing(settings=settings, service_name=settings.app_name)

    await run_migrations(str(settings.postgres_dsn))

    db_pool = await asyncpg.create_pool(
        dsn=str(settings.postgres_dsn),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=settings.db_pool_command_timeout_seconds,
        server_settings={
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "idle_in_transaction_session_timeout": str(settings.db_idle_in_transaction_timeout_ms),
        },
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime_seconds,
    )
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    try:
        await redis_client.ping()

        app.state.runtime = RuntimeState(
            settings=settings,
            db_pool=db_pool,
            redis_client=redis_client,
        )
        app.state.dependency_health = build_dependency_health_service(
            settings,
            db_pool=db_pool,
            redis_client=redis_client,
        )
        await _rehydrate_revocation_cache(
            db_pool=db_pool,
            redis_client=redis_client,
            bucket_ttl_seconds=settings.revocation_bucket_ttl_seconds,
        )
    except Exception:
        await redis_client.close()
        await db_pool.close()
        raise

    logger.info("startup_complete", app_name=settings.app_name)

    yield

    await redis_client.close()
    await db_pool.close()
    logger.info("shutdown_complete")


def create_app(
    settings: AppSettings | None = None,
    dependency_health: DependencyHealthService | None = None,
) -> FastAPI:
    """Create the FastAPI application for Solution 2."""

    # Optional injection is preserved for testing, but runtime uses lifespan initialization.
    _ = settings
    _ = dependency_health

    app = FastAPI(title="mc-solution2-api", lifespan=_lifespan)

    @app.middleware("http")
    async def _metrics_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        raw_path = request.url.path
        method = request.method
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        request.state.trace_id = trace_id
        clear_log_context()
        bind_log_context(trace_id=trace_id, path=raw_path, method=method)
        start = time.perf_counter()
        carrier = {key: value for key, value in request.headers.items()}
        with start_span(
            tracer_name="solution2.api",
            span_name=f"{method} {raw_path}",
            kind=SpanKind.SERVER,
            attributes={"http.method": method, "http.route.raw": raw_path},
            parent_carrier=carrier,
        ) as span:
            try:
                response = await call_next(request)
            except Exception:
                canonical_path = _canonical_path_label(request)
                duration = time.perf_counter() - start
                HTTP_REQUESTS_TOTAL.labels(method=method, path=canonical_path, status="500").inc()
                HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=canonical_path).observe(
                    duration
                )
                span.set_attribute("http.status_code", 500)
                raise
            finally:
                clear_log_context()

            canonical_path = _canonical_path_label(request)
            duration = time.perf_counter() - start
            HTTP_REQUESTS_TOTAL.labels(
                method=method, path=canonical_path, status=str(response.status_code)
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=canonical_path).observe(
                duration
            )
            span.set_attribute("http.route", canonical_path)
            span.set_attribute("http.status_code", response.status_code)
        return response

    @app.post(V1_OAUTH_TOKEN_PATH, response_model=OAuthTokenResponse)
    async def oauth_token(payload: OAuthTokenRequest, request: Request) -> JSONResponse:
        """Exchange deterministic local credentials for an OAuth access token."""
        runtime = _runtime_state(request)
        retry_after = await _check_oauth_token_rate_limit(payload=payload, request=request)
        if retry_after is not None:
            return _error_response(
                status_code=429,
                code="TOO_MANY_REQUESTS",
                message="OAuth token rate limit exceeded",
                retry_after=retry_after,
            )

        if payload.api_key is not None:
            try:
                api_key_valid = await _validate_oauth_api_key(
                    api_key=payload.api_key,
                    request=request,
                )
            except Exception as exc:
                logger.exception("oauth_api_key_validation_failed", error=str(exc))
                return _error_response(
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Service temporarily unavailable",
                )
            if not api_key_valid:
                return _error_response(
                    status_code=401,
                    code="UNAUTHORIZED",
                    message="Invalid OAuth credentials",
                )

        try:
            client_id, client_secret = _resolve_oauth_client_credentials(
                payload=payload,
                request=request,
            )
        except ValueError as exc:
            return _error_response(status_code=400, code="BAD_REQUEST", message=str(exc))

        scope = payload.scope or runtime.settings.oauth_default_scope
        token_payload = await _exchange_client_credentials_for_token(
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            request=request,
        )
        TOKEN_ISSUANCE_TOTAL.labels(grant_type="client_credentials").inc()
        response = OAuthTokenResponse(
            access_token=token_payload["access_token"],
            token_type=token_payload["token_type"],
            expires_in=token_payload["expires_in"],
            scope=cast(str | None, token_payload.get("scope")),
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    @app.post(V1_AUTH_REVOKE_PATH, response_model=RevokeTokenResponse)
    async def revoke_auth_token(request: Request) -> JSONResponse:
        current_user = await _authenticate(request)
        runtime = _runtime_state(request)

        claims = getattr(request.state, "jwt_claims", None)
        if not isinstance(claims, dict):
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti.strip():
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        expires_at = _jwt_expiry_from_claims(claims)
        if expires_at is None:
            raise HTTPException(status_code=401, detail="Invalid bearer token")

        try:
            await revoke_jti(
                redis_client=runtime.redis_client,
                pool=runtime.db_pool,
                user_id=current_user.user_id,
                jti=jti.strip(),
                expires_at=expires_at,
                bucket_ttl=runtime.settings.revocation_bucket_ttl_seconds,
            )
        except Exception as exc:
            logger.exception("token_revoke_failed", error=str(exc))
            raise HTTPException(status_code=503, detail="Service temporarily unavailable") from exc

        TOKEN_REVOCATIONS_TOTAL.inc()
        response = RevokeTokenResponse(revoked=True)
        return JSONResponse(status_code=200, content=response.model_dump())

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 401:
            if str(exc.detail) == "TOKEN_EXPIRED":
                return _error_response(
                    status_code=401,
                    code="TOKEN_EXPIRED",
                    message="Bearer token expired; request a new access token",
                )
            return _error_response(
                status_code=401,
                code="UNAUTHORIZED",
                message="Missing or invalid bearer token",
            )
        if exc.status_code == 403:
            return _error_response(
                status_code=403,
                code="FORBIDDEN",
                message=str(exc.detail) if exc.detail else "Forbidden",
            )
        if exc.status_code == 503:
            return _error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if exc.status_code == 404:
            return _error_response(status_code=404, code="NOT_FOUND", message="Resource not found")
        if exc.status_code == 409:
            return _error_response(status_code=409, code="CONFLICT", message=str(exc.detail))
        return _error_response(
            status_code=exc.status_code,
            code="HTTP_ERROR",
            message=str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        detail = exc.errors()[0]["msg"] if exc.errors() else "Invalid request"
        return _error_response(status_code=400, code="BAD_REQUEST", message=str(detail))

    app_module = sys.modules[__name__]
    register_system_routes(app, cast(SystemRoutesApp, app_module))
    register_task_read_routes(app, cast(TaskReadRoutesApp, app_module))
    register_task_write_routes(app, cast(TaskWriteRoutesApp, app_module))
    register_admin_routes(app, cast(AdminRoutesApp, app_module))
    register_webhook_routes(app, cast(WebhookRoutesApp, app_module))

    return app
