"""Shared enums and constants used by API, DB migrations, and workers."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class UserRole(StrEnum):
    """Role values persisted in `users.role`."""

    ADMIN = "admin"
    USER = "user"


class SubscriptionTier(StrEnum):
    """Supported subscription tier values for Solution 2."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class RequestMode(StrEnum):
    """Accepted request modes for Sol2."""

    ASYNC = "async"
    SYNC = "sync"
    BATCH = "batch"


class ReservationState(StrEnum):
    """Reservation lifecycle states for Sol2 credit correctness."""

    RESERVED = "RESERVED"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"


class ModelClass(StrEnum):
    """Supported simulated model classes for Solution 2."""

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
    TIMEOUT = "TIMEOUT"
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

Tier: type[SubscriptionTier] = SubscriptionTier

MODEL_CLASS_VALUES: Final[tuple[str, ...]] = tuple(model.value for model in ModelClass)
MODEL_CLASS_VALUES_SQL: Final[str] = _sql_quoted_csv(MODEL_CLASS_VALUES)
DEFAULT_MODEL_CLASS: Final[str] = ModelClass.SMALL.value

TIER_CONCURRENCY_MULTIPLIER: Final[dict[SubscriptionTier, int]] = {
    SubscriptionTier.FREE: 1,
    SubscriptionTier.PRO: 2,
    SubscriptionTier.ENTERPRISE: 4,
}

RESERVATION_TRANSITIONS: Final[dict[ReservationState, frozenset[ReservationState]]] = {
    ReservationState.RESERVED: frozenset(
        {ReservationState.CAPTURED, ReservationState.RELEASED},
    ),
    ReservationState.CAPTURED: frozenset(),
    ReservationState.RELEASED: frozenset(),
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
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.TIMEOUT.value,
        TaskStatus.EXPIRED.value,
    }
)

TASK_STATE_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
            TaskStatus.EXPIRED,
        }
    ),
    TaskStatus.COMPLETED: frozenset({TaskStatus.EXPIRED}),
    TaskStatus.FAILED: frozenset({TaskStatus.EXPIRED}),
    TaskStatus.CANCELLED: frozenset({TaskStatus.EXPIRED}),
    TaskStatus.TIMEOUT: frozenset({TaskStatus.EXPIRED}),
    TaskStatus.EXPIRED: frozenset(),
}

SEED_ADMIN_CREDITS: Final[int] = 1000
SEED_TEST_USER1_CREDITS: Final[int] = 500
SEED_TEST_USER2_CREDITS: Final[int] = 250

SEED_ADMIN_NAME: Final[str] = "admin"
SEED_ALICE_NAME: Final[str] = "alice"
SEED_BOB_NAME: Final[str] = "bob"


def task_cost_for_model(*, base_cost: int, model_class: ModelClass) -> int:
    """Compute effective task cost using model-class multiplier."""

    return max(1, base_cost * MODEL_COST_MULTIPLIER[model_class])


def compute_routing_key(
    mode: str | RequestMode,
    tier: str | SubscriptionTier,
    model_class: str | ModelClass,
) -> str:
    """Build the RabbitMQ routing key for task dispatch."""

    return (
        f"tasks.{RequestMode(mode).value}."
        f"{SubscriptionTier(tier).value}."
        f"{ModelClass(model_class).value}"
    )


def resolve_queue(
    *,
    tier: str | SubscriptionTier,
    mode: str | RequestMode,
    model_class: str | ModelClass,
) -> str:
    """Resolve target queue from mode/tier/model policy."""

    resolved_tier = SubscriptionTier(tier)
    resolved_mode = RequestMode(mode)
    _resolved_model_class = ModelClass(model_class)
    if resolved_tier == Tier.FREE:
        if resolved_mode == RequestMode.SYNC:
            raise ValueError("free tier does not support sync")
        return "queue.batch"
    if resolved_tier == Tier.PRO:
        if resolved_mode == RequestMode.SYNC and _resolved_model_class != ModelClass.SMALL:
            raise ValueError("pro tier sync is restricted to small model")
        if resolved_mode == RequestMode.BATCH:
            return "queue.batch"
        return "queue.fast"
    if resolved_tier == Tier.ENTERPRISE:
        if resolved_mode == RequestMode.BATCH:
            return "queue.fast"
        return "queue.realtime"
    raise ValueError(f"unsupported tier: {resolved_tier.value}")


def is_valid_reservation_transition(
    *,
    current_state: ReservationState,
    next_state: ReservationState,
) -> bool:
    """Validate legal reservation state transitions."""

    return next_state in RESERVATION_TRANSITIONS[current_state]


def is_valid_task_transition(
    *,
    current_state: TaskStatus,
    next_state: TaskStatus,
) -> bool:
    """Validate legal task lifecycle transitions."""

    return next_state in TASK_STATE_TRANSITIONS[current_state]


def max_concurrent_for_tier(*, base_max_concurrent: int, tier: SubscriptionTier) -> int:
    """Compute effective max concurrency using tier multiplier."""

    return max(1, base_max_concurrent * TIER_CONCURRENCY_MULTIPLIER[tier])


def runtime_seconds_for_model(model_class: ModelClass) -> float:
    """Return simulated model runtime used by async workers."""

    return MODEL_RUNTIME_SECONDS[model_class]
