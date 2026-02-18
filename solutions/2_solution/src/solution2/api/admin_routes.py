"""Admin HTTP routes for credit top-up and manual adjustments."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from solution2.api.contracts import AdminRoutesApp
from solution2.api.error_responses import api_error_response
from solution2.api.paths import COMPAT_ADMIN_CREDITS_PATH, V1_ADMIN_CREDITS_PATH
from solution2.constants import OAuthScope
from solution2.models.domain import AuthUser
from solution2.models.schemas import AdminCreditsRequest, AdminCreditsResponse
from solution2.utils.retry import retry_async


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def register_admin_routes(app: FastAPI, app_module: AdminRoutesApp) -> None:
    """Register admin-only routes."""

    @app.post(COMPAT_ADMIN_CREDITS_PATH, response_model=AdminCreditsResponse, tags=["compat"])
    @app.post(V1_ADMIN_CREDITS_PATH, response_model=AdminCreditsResponse)
    async def admin_credits(
        payload: AdminCreditsRequest,
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        """Apply a signed credit delta for a target API key."""
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.ADMIN_CREDITS.value}),
        )
        runtime = app_module._runtime_state(request)

        is_admin = (
            current_user.role == app_module.ADMIN_ROLE
            or current_user.api_key == runtime.settings.admin_api_key
        )
        if not is_admin:
            return api_error_response(
                status_code=403, code="FORBIDDEN", message="Admin access required"
            )
        if payload.api_key is None:
            return api_error_response(
                status_code=400,
                code="BAD_REQUEST",
                message="admin credits endpoint requires api_key target",
            )
        target_api_key = payload.api_key

        try:
            async with runtime.db_pool.acquire() as connection, connection.transaction():
                outcome = await app_module.admin_update_user_credits(
                    connection,
                    target_api_key=target_api_key,
                    delta=payload.delta,
                    reason=payload.reason,
                )
                if outcome is None:
                    return api_error_response(
                        status_code=404,
                        code="NOT_FOUND",
                        message="User not found",
                    )

                user_id, old_balance, new_balance = outcome
                await app_module.create_outbox_event(
                    connection,
                    aggregate_id=user_id,
                    event_type="credits.adjusted",
                    routing_key="admin.credits.adjusted",
                    payload={
                        "user_id": str(user_id),
                        "old_credits": old_balance,
                        "new_credits": new_balance,
                        "delta": payload.delta,
                        "reason": payload.reason,
                        "admin_id": str(current_user.user_id),
                    },
                )
        except Exception:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        try:
            retry_attempts = int(getattr(runtime.settings, "redis_retry_attempts", 3))
            retry_base_delay = float(
                getattr(runtime.settings, "redis_retry_base_delay_seconds", 0.05)
            )
            retry_max_delay = float(getattr(runtime.settings, "redis_retry_max_delay_seconds", 0.5))

            async def _sync_cache() -> None:
                await app_module.invalidate_user_auth_cache(
                    api_key=target_api_key,
                    redis_client=runtime.redis_client,
                )

            await retry_async(
                _sync_cache,
                attempts=retry_attempts,
                base_delay_seconds=retry_base_delay,
                max_delay_seconds=retry_max_delay,
            )
        except Exception as exc:
            app_module.logger.warning(
                "admin_credit_cache_sync_failed",
                target_api_key_masked=_mask_api_key(target_api_key),
                user_id=str(user_id),
                error=str(exc),
            )
        app_module.CREDIT_DEDUCTIONS_TOTAL.labels(reason=payload.reason).inc()
        app_module.logger.info(
            "business_event_admin_credit_adjusted",
            target_api_key_masked=_mask_api_key(target_api_key),
            delta=payload.delta,
            reason=payload.reason,
            new_balance=new_balance,
            actor_user_id=str(current_user.user_id),
        )

        response = AdminCreditsResponse(api_key=target_api_key, new_balance=new_balance)
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
