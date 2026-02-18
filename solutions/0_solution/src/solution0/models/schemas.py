from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


class SubmitTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=_INT32_MIN, le=_INT32_MAX)
    y: int = Field(ge=_INT32_MIN, le=_INT32_MAX)


class SubmitTaskResponse(BaseModel):
    task_id: UUID
    status: str
    expires_at: datetime


class PollTaskResponse(BaseModel):
    task_id: UUID
    status: str
    result: dict[str, Any] | None
    error: str | None
    queue_position: int | None
    estimated_seconds: int | None
    expires_at: datetime | None


class CancelTaskResponse(BaseModel):
    task_id: UUID
    status: str
    credits_refunded: int


class AdminCreditsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=36, max_length=36)
    delta: int
    reason: str = Field(min_length=1, max_length=64)


class AdminCreditsResponse(BaseModel):
    api_key: str
    new_balance: int


class ErrorPayload(BaseModel):
    code: str
    message: str
    retry_after: int | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorPayload


class ReadyResponse(BaseModel):
    ready: bool
    dependencies: dict[str, bool]
    trace_id: str
