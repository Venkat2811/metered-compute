from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from solution3.constants import ModelClass, RequestMode

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


class HealthResponse(BaseModel):
    status: str
    solution: str
    timestamp: str


class ReadyResponse(BaseModel):
    ready: bool
    dependencies: list[str] = Field(default_factory=list)
    checked_at: str

    @classmethod
    def with_defaults(cls, *, ready: bool, deps: list[str] | None = None) -> ReadyResponse:
        return cls(ready=ready, dependencies=deps or [], checked_at=datetime.now(UTC).isoformat())


class SubmitTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=_INT32_MIN, le=_INT32_MAX)
    y: int = Field(ge=_INT32_MIN, le=_INT32_MAX)
    mode: RequestMode = RequestMode.ASYNC
    model_class: ModelClass = ModelClass.SMALL
    callback_url: str | None = Field(default=None, min_length=1, max_length=2048)


class SubmitTaskResponse(BaseModel):
    task_id: UUID
    status: str
    billing_state: str
    queue: str | None = None
    expires_at: datetime


class PollTaskResponse(BaseModel):
    task_id: UUID
    status: str
    billing_state: str
    result: dict[str, Any] | None
    error: str | None
    expires_at: datetime | None


class CancelTaskResponse(BaseModel):
    task_id: UUID
    status: str
    billing_state: str


class AdminCreditsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=36, max_length=36)
    amount: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=64)
    transfer_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class AdminCreditsResponse(BaseModel):
    api_key: str
    new_balance: int


class OAuthTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str | None = None
    client_secret: str | None = None
    api_key: str | None = None
    scope: str | None = None

    @model_validator(mode="after")
    def _validate_credential_shape(self) -> OAuthTokenRequest:
        if self.api_key:
            if self.client_id or self.client_secret:
                raise ValueError("api_key cannot be combined with client credentials")
            return self
        if bool(self.client_id) != bool(self.client_secret):
            raise ValueError("client_id and client_secret are required together")
        if not self.client_id and not self.client_secret:
            raise ValueError("provide api_key or client credentials")
        return self


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    scope: str | None = None


class ErrorPayload(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorPayload
