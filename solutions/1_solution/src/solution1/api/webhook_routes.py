"""Webhook registration routes for per-user callback subscriptions."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from solution1.api.contracts import WebhookRoutesApp
from solution1.api.error_responses import api_error_response
from solution1.api.paths import V1_WEBHOOK_PATH
from solution1.constants import OAuthScope
from solution1.models.domain import AuthUser
from solution1.models.schemas import WebhookConfigRequest, WebhookConfigResponse
from solution1.services.webhooks import is_valid_callback_url


def register_webhook_routes(app: FastAPI, app_module: WebhookRoutesApp) -> None:
    """Register webhook subscription management endpoints."""

    @app.put(V1_WEBHOOK_PATH, response_model=WebhookConfigResponse)
    async def put_webhook(
        payload: WebhookConfigRequest,
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.TASK_POLL.value}),
        )
        callback_url = payload.callback_url.strip()
        if not is_valid_callback_url(callback_url):
            return api_error_response(
                status_code=400,
                code="BAD_REQUEST",
                message="Invalid callback_url: must be absolute http(s) URL",
            )

        runtime = app_module._runtime_state(request)
        try:
            subscription = await app_module.upsert_webhook_subscription(
                runtime.db_pool,
                user_id=current_user.user_id,
                callback_url=callback_url,
                enabled=payload.enabled,
            )
        except Exception as exc:
            app_module.logger.exception("webhook_upsert_failed", error=str(exc))
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )

        response = WebhookConfigResponse(
            callback_url=subscription.callback_url,
            enabled=subscription.enabled,
            updated_at=subscription.updated_at,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    @app.get(V1_WEBHOOK_PATH, response_model=WebhookConfigResponse)
    async def get_webhook(
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.TASK_POLL.value}),
        )
        runtime = app_module._runtime_state(request)
        try:
            subscription = await app_module.get_webhook_subscription(
                runtime.db_pool,
                user_id=current_user.user_id,
            )
        except Exception as exc:
            app_module.logger.exception("webhook_get_failed", error=str(exc))
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if subscription is None:
            return api_error_response(
                status_code=404,
                code="NOT_FOUND",
                message="Webhook subscription not found",
            )

        response = WebhookConfigResponse(
            callback_url=subscription.callback_url,
            enabled=subscription.enabled,
            updated_at=subscription.updated_at,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    @app.delete(V1_WEBHOOK_PATH, response_model=WebhookConfigResponse)
    async def delete_webhook(
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.TASK_POLL.value}),
        )
        runtime = app_module._runtime_state(request)
        try:
            subscription = await app_module.disable_webhook_subscription(
                runtime.db_pool,
                user_id=current_user.user_id,
            )
        except Exception as exc:
            app_module.logger.exception("webhook_delete_failed", error=str(exc))
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if subscription is None:
            return api_error_response(
                status_code=404,
                code="NOT_FOUND",
                message="Webhook subscription not found",
            )

        response = WebhookConfigResponse(
            callback_url=subscription.callback_url,
            enabled=subscription.enabled,
            updated_at=subscription.updated_at,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
