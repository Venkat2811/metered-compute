from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from solution1.constants import SubscriptionTier, TaskStatus, UserRole


@dataclass(frozen=True)
class AuthUser:
    """Authenticated user context resolved from API key."""

    api_key: str
    user_id: UUID
    name: str
    role: UserRole
    credits: int
    tier: SubscriptionTier = SubscriptionTier.FREE
    scopes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TaskRecord:
    """Task projection model used by API and worker layers."""

    task_id: UUID
    api_key: str
    user_id: UUID
    x: int
    y: int
    cost: int
    status: TaskStatus
    result: dict[str, Any] | None
    error: str | None
    runtime_ms: int | None
    idempotency_key: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class WebhookSubscription:
    """Webhook callback subscription persisted for a user."""

    user_id: UUID
    callback_url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AdmissionDecision:
    """Outcome of the Redis admission gate for a submit request."""

    ok: bool
    reason: str
    existing_task_id: str | None


@dataclass(frozen=True)
class WebhookTerminalEvent:
    """Terminal webhook event payload queued for async delivery."""

    event_id: str
    user_id: str
    task_id: str
    status: str
    result: dict[str, Any] | None
    error: str | None
    occurred_at_epoch: int
    attempt: int = 0
    last_error: str | None = None
