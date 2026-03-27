from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from uuid6 import uuid7

from solution3.api.error_responses import api_error_response
from solution3.api.paths import V1_ADMIN_CREDITS_PATH
from solution3.constants import UserRole
from solution3.db.repository import fetch_active_user_by_api_key, record_admin_credit_topup
from solution3.models.domain import AuthUser
from solution3.models.schemas import AdminCreditsRequest, AdminCreditsResponse
from solution3.services.auth import (
    require_authenticated_user,
    require_scopes,
    runtime_state_from_request,
)
from solution3.utils.logging import get_logger

AUTHENTICATED_USER = Depends(require_authenticated_user)
logger = get_logger("solution3.admin")


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def register_admin_routes(router: APIRouter) -> None:
    @router.post(V1_ADMIN_CREDITS_PATH, response_model=AdminCreditsResponse, tags=["admin"])
    async def admin_credits(
        payload: AdminCreditsRequest,
        request: Request,
        current_user: AuthUser = AUTHENTICATED_USER,
    ) -> JSONResponse:
        require_scopes(current_user=current_user, required_scopes=frozenset({"admin:credits"}))
        if current_user.role != UserRole.ADMIN:
            return api_error_response(
                status_code=403,
                code="FORBIDDEN",
                message="Admin role required",
            )
        runtime = runtime_state_from_request(request)
        if runtime.db_pool is None or runtime.billing_client is None:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Billing backend unavailable",
            )

        target_user = await fetch_active_user_by_api_key(runtime.db_pool, api_key=payload.api_key)
        if target_user is None:
            return api_error_response(
                status_code=404,
                code="NOT_FOUND",
                message="User not found",
            )

        transfer_id = uuid7()
        try:
            await asyncio.to_thread(
                runtime.billing_client.ensure_user_account,
                target_user.user_id,
                initial_credits=0,
            )
            topup_ok = await asyncio.to_thread(
                runtime.billing_client.topup_credits,
                user_id=target_user.user_id,
                transfer_id=transfer_id,
                amount=payload.amount,
            )
            if not topup_ok:
                return api_error_response(
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Billing backend unavailable",
                )
            new_balance = await asyncio.to_thread(
                runtime.billing_client.get_balance,
                target_user.user_id,
            )
            await record_admin_credit_topup(
                runtime.db_pool,
                user_id=target_user.user_id,
                amount=payload.amount,
                reason=payload.reason,
                admin_user_id=current_user.user_id,
                api_key=payload.api_key,
                new_balance=new_balance,
                transfer_id=transfer_id,
            )
        except Exception as exc:
            logger.error(
                "solution3_admin_credit_outbox_failed",
                target_api_key_masked=_mask_api_key(payload.api_key),
                target_user_id=str(target_user.user_id),
                admin_user_id=str(current_user.user_id),
                error=str(exc),
            )
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Billing backend unavailable",
            )

        response = AdminCreditsResponse(api_key=payload.api_key, new_balance=int(new_balance))
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
