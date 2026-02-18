from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from solution2.constants import ModelClass, RequestMode

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


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
    estimated_seconds: int
    result: dict[str, Any] | None = None
    error: str | None = None
    runtime_ms: int | None = None
    queue: str | None = None
    expires_at: datetime


class BatchSubmitTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=_INT32_MIN, le=_INT32_MAX)
    y: int = Field(ge=_INT32_MIN, le=_INT32_MAX)
    model_class: ModelClass = ModelClass.SMALL


class BatchSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[BatchSubmitTask] = Field(min_length=1, max_length=100)


class BatchSubmitResponse(BaseModel):
    batch_id: UUID
    task_ids: list[UUID]
    total_cost: int


class PollTaskResponse(BaseModel):
    task_id: UUID
    status: str
    result: dict[str, Any] | None
    error: str | None
    queue: str | None = None
    queue_position: int | None
    estimated_seconds: int | None
    expires_at: datetime | None


class CancelTaskResponse(BaseModel):
    task_id: UUID
    status: str
    credits_refunded: int


class AdminCreditsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None = None
    api_key: str | None = Field(default=None, min_length=36, max_length=36)
    delta: int
    reason: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_target_identity(self) -> AdminCreditsRequest:
        if (self.user_id is None and not self.api_key) or (
            self.user_id is not None and self.api_key is not None
        ):
            raise ValueError("provide exactly one of user_id or api_key")
        return self


class AdminCreditsResponse(BaseModel):
    user_id: UUID | None = None
    api_key: str | None = None
    new_balance: int


class WebhookConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback_url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True


class WebhookConfigResponse(BaseModel):
    callback_url: str
    enabled: bool
    updated_at: datetime


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


class RevokeTokenResponse(BaseModel):
    revoked: bool


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
