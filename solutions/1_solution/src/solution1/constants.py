"""Shared enums and constants used by API, DB migrations, and workers."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class UserRole(StrEnum):
    """Role values persisted in `users.role`."""

    ADMIN = "admin"
    USER = "user"


class SubscriptionTier(StrEnum):
    """Supported subscription tier values for Solution 1."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class ModelClass(StrEnum):
    """Supported simulated model classes for Solution 1."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class OAuthScope(StrEnum):
    """OAuth scope values used for route-level authorization."""

    TASK_SUBMIT = "task:submit"
    TASK_POLL = "task:poll"
    TASK_CANCEL = "task:cancel"
    ADMIN_CREDITS = "admin:credits"


class TaskStatus(StrEnum):
    """Persisted task lifecycle states."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TaskCompletionMetricStatus(StrEnum):
    """Worker metric labels for terminal/short-circuit outcomes."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _sql_quoted_csv(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


USER_ROLE_VALUES: Final[tuple[str, ...]] = tuple(role.value for role in UserRole)
USER_ROLE_VALUES_SQL: Final[str] = _sql_quoted_csv(USER_ROLE_VALUES)
DEFAULT_USER_ROLE: Final[str] = UserRole.USER.value
ADMIN_ROLE: Final[str] = UserRole.ADMIN.value

TIER_VALUES: Final[tuple[str, ...]] = tuple(tier.value for tier in SubscriptionTier)
TIER_VALUES_SQL: Final[str] = _sql_quoted_csv(TIER_VALUES)
DEFAULT_TIER: Final[str] = SubscriptionTier.FREE.value
ADMIN_TIER: Final[str] = SubscriptionTier.ENTERPRISE.value

MODEL_CLASS_VALUES: Final[tuple[str, ...]] = tuple(model.value for model in ModelClass)
MODEL_CLASS_VALUES_SQL: Final[str] = _sql_quoted_csv(MODEL_CLASS_VALUES)
DEFAULT_MODEL_CLASS: Final[str] = ModelClass.SMALL.value

TIER_CONCURRENCY_MULTIPLIER: Final[dict[SubscriptionTier, int]] = {
    SubscriptionTier.FREE: 1,
    SubscriptionTier.PRO: 2,
    SubscriptionTier.ENTERPRISE: 4,
}

MODEL_COST_MULTIPLIER: Final[dict[ModelClass, int]] = {
    ModelClass.SMALL: 1,
    ModelClass.MEDIUM: 2,
    ModelClass.LARGE: 5,
}

MODEL_RUNTIME_SECONDS: Final[dict[ModelClass, float]] = {
    ModelClass.SMALL: 2.0,
    ModelClass.MEDIUM: 4.0,
    ModelClass.LARGE: 7.0,
}
MAX_MODEL_RUNTIME_SECONDS: Final[float] = max(MODEL_RUNTIME_SECONDS.values())
STREAM_RECLAIM_RUNTIME_BUFFER_SECONDS: Final[float] = 8.0
STREAM_HEARTBEAT_RUNTIME_BUFFER_SECONDS: Final[float] = 2.0

TASK_STATUS_VALUES: Final[tuple[str, ...]] = tuple(status.value for status in TaskStatus)
TASK_STATUS_VALUES_SQL: Final[str] = _sql_quoted_csv(TASK_STATUS_VALUES)
DEFAULT_TASK_STATUS: Final[str] = TaskStatus.PENDING.value
TASK_CANCELLABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
)
TASK_RUNNING_STATUSES: Final[frozenset[str]] = frozenset(
    {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
)
TASK_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
)

SEED_ADMIN_CREDITS: Final[int] = 1000
SEED_TEST_USER1_CREDITS: Final[int] = 500
SEED_TEST_USER2_CREDITS: Final[int] = 250

SEED_ADMIN_NAME: Final[str] = "admin"
SEED_ALICE_NAME: Final[str] = "alice"
SEED_BOB_NAME: Final[str] = "bob"


def task_cost_for_model(*, base_cost: int, model_class: ModelClass) -> int:
    """Compute effective task cost using model-class multiplier."""

    return max(1, base_cost * MODEL_COST_MULTIPLIER[model_class])


def max_concurrent_for_tier(*, base_max_concurrent: int, tier: SubscriptionTier) -> int:
    """Compute effective max concurrency using tier multiplier."""

    return max(1, base_max_concurrent * TIER_CONCURRENCY_MULTIPLIER[tier])


def runtime_seconds_for_model(model_class: ModelClass) -> float:
    """Return simulated model runtime used by stream workers."""

    return MODEL_RUNTIME_SECONDS[model_class]


def minimum_stream_claim_idle_ms() -> int:
    """Minimum safe XAUTOCLAIM idle window for modeled runtimes."""

    return int((MAX_MODEL_RUNTIME_SECONDS + STREAM_RECLAIM_RUNTIME_BUFFER_SECONDS) * 1000)


def minimum_worker_heartbeat_ttl_seconds(*, block_ms: int) -> int:
    """Minimum heartbeat TTL to avoid false negatives while worker blocks/executes."""

    return int(
        (block_ms / 1000.0) + MAX_MODEL_RUNTIME_SECONDS + STREAM_HEARTBEAT_RUNTIME_BUFFER_SECONDS
    )
