from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from solution3.constants import (
    BillingState,
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)


@dataclass(frozen=True, slots=True)
class AuthUser:
    api_key: str
    user_id: UUID
    name: str
    role: UserRole
    tier: SubscriptionTier
    scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class TaskCommand:
    task_id: UUID
    user_id: UUID
    tier: SubscriptionTier
    mode: RequestMode
    model_class: ModelClass
    status: TaskStatus
    billing_state: BillingState
    x: int
    y: int
    cost: int
    tb_pending_transfer_id: UUID
    callback_url: str | None
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TaskQueryView:
    task_id: UUID
    user_id: UUID
    tier: SubscriptionTier
    mode: RequestMode
    model_class: ModelClass
    status: TaskStatus
    billing_state: BillingState
    result: dict[str, Any] | None
    error: str | None
    runtime_ms: int | None
    projection_version: int
    created_at: datetime
    updated_at: datetime
