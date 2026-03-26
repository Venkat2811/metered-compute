from __future__ import annotations

from fastapi import APIRouter, Request

from solution3.api.error_responses import api_error_response
from solution3.api.paths import V1_OAUTH_TOKEN_PATH
from solution3.models.schemas import OAuthTokenRequest, OAuthTokenResponse
from solution3.services.auth import (
    exchange_client_credentials_for_token as _exchange_client_credentials_for_token,
)
from solution3.services.auth import (
    resolve_oauth_client_credentials,
    runtime_state_from_request,
)
from solution3.services.auth import (
    validate_oauth_api_key as _validate_oauth_api_key,
)

DEFAULT_SCOPE = "task:submit task:poll task:cancel admin:credits"


def register_auth_routes(router: APIRouter) -> None:
    @router.post(V1_OAUTH_TOKEN_PATH, tags=["auth"], response_model=OAuthTokenResponse)
    async def oauth_token(payload: OAuthTokenRequest, request: Request) -> object:
        if payload.api_key and not await _validate_oauth_api_key(
            api_key=payload.api_key, request=request
        ):
            return api_error_response(
                status_code=401,
                code="UNAUTHORIZED",
                message="Invalid OAuth credentials",
            )

        runtime = runtime_state_from_request(request)
        client_id, client_secret = resolve_oauth_client_credentials(
            payload=payload, runtime=runtime
        )
        token_payload = await _exchange_client_credentials_for_token(
            client_id=client_id,
            client_secret=client_secret,
            scope=payload.scope or DEFAULT_SCOPE,
            request=request,
        )
        return OAuthTokenResponse(**token_payload)
