from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from solution0.constants import TaskStatus, UserRole


@dataclass(frozen=True)
class AuthUser:
    """Authenticated user context resolved from API key."""

    api_key: str
    user_id: UUID
    name: str
    role: UserRole
    credits: int


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
class AdmissionDecision:
    """Outcome of the Redis admission gate for a submit request."""

    ok: bool
    reason: str
    existing_task_id: str | None
