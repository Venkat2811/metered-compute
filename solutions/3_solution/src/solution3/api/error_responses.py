from __future__ import annotations

from fastapi.responses import JSONResponse

from solution3.models.schemas import ErrorEnvelope, ErrorPayload


def api_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    payload = ErrorEnvelope(error=ErrorPayload(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())
