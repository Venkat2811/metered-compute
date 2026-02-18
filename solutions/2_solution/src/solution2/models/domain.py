from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from solution2.constants import (
    ModelClass,
    RequestMode,
    ReservationState,
    SubscriptionTier,
    TaskStatus,
    UserRole,
)


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


@dataclass(frozen=True)
class TaskCommand:
    """Sol2 write-side command representation projected from DB rows."""

    task_id: UUID
    user_id: UUID
    tier: SubscriptionTier
    mode: RequestMode
    model_class: ModelClass
    status: TaskStatus
    x: int
    y: int
    cost: int
    callback_url: str | None
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CreditReservation:
    """Sol2 reservation row used by credit guardrails."""

    reservation_id: UUID
    task_id: UUID
    user_id: UUID
    amount: int
    state: ReservationState
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OutboxEvent:
    """Sol2 outbox event row."""

    event_id: UUID
    aggregate_id: UUID
    event_type: str
    routing_key: str
    payload: dict[str, Any]
    published_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class TaskQueryView:
    """CQRS query projection row for task status and result."""

    task_id: UUID
    user_id: UUID
    tier: SubscriptionTier
    mode: RequestMode
    model_class: ModelClass
    status: TaskStatus
    result: dict[str, Any] | None
    error: str | None
    queue_name: str | None
    runtime_ms: int | None
    created_at: datetime
    updated_at: datetime
