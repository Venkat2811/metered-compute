from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from solution3.api.error_responses import api_error_response
from solution3.api.paths import V1_ADMIN_CREDITS_PATH
from solution3.constants import UserRole
from solution3.models.domain import AuthUser
from solution3.models.schemas import AdminCreditsRequest
from solution3.services.auth import require_authenticated_user, require_scopes

AUTHENTICATED_USER = Depends(require_authenticated_user)


def register_admin_routes(router: APIRouter) -> None:
    @router.post(V1_ADMIN_CREDITS_PATH, tags=["admin"])
    async def admin_credits(
        _payload: AdminCreditsRequest,
        _request: Request,
        current_user: AuthUser = AUTHENTICATED_USER,
    ) -> JSONResponse:
        require_scopes(current_user=current_user, required_scopes=frozenset({"admin:credits"}))
        if current_user.role != UserRole.ADMIN:
            return api_error_response(
                status_code=403,
                code="FORBIDDEN",
                message="Admin role required",
            )
        return api_error_response(
            status_code=503,
            code="SERVICE_DEGRADED",
            message="TigerBeetle admin credit path is not initialized yet",
        )
