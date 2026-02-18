"""Shared API error-response helpers for Solution 1 routes."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from solution1.models.schemas import ErrorEnvelope, ErrorPayload


def api_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retry_after: int | None = None,
) -> JSONResponse:
    """Build the canonical JSON error envelope used by all API routes."""
    payload = ErrorEnvelope(
        error=ErrorPayload(code=code, message=message, retry_after=retry_after),
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())
