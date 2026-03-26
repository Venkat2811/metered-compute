"""Shared enums and constants for Solution 3 runtime and SQL templates."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class SubscriptionTier(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class RequestMode(StrEnum):
    ASYNC = "async"
    SYNC = "sync"
    BATCH = "batch"


class ModelClass(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class BillingState(StrEnum):
    RESERVED = "RESERVED"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


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

REQUEST_MODE_VALUES: Final[tuple[str, ...]] = tuple(mode.value for mode in RequestMode)
REQUEST_MODE_VALUES_SQL: Final[str] = _sql_quoted_csv(REQUEST_MODE_VALUES)
DEFAULT_REQUEST_MODE: Final[str] = RequestMode.ASYNC.value

MODEL_CLASS_VALUES: Final[tuple[str, ...]] = tuple(model.value for model in ModelClass)
MODEL_CLASS_VALUES_SQL: Final[str] = _sql_quoted_csv(MODEL_CLASS_VALUES)
DEFAULT_MODEL_CLASS: Final[str] = ModelClass.SMALL.value

TASK_STATUS_VALUES: Final[tuple[str, ...]] = tuple(status.value for status in TaskStatus)
TASK_STATUS_VALUES_SQL: Final[str] = _sql_quoted_csv(TASK_STATUS_VALUES)
DEFAULT_TASK_STATUS: Final[str] = TaskStatus.PENDING.value
TASK_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.EXPIRED.value,
    }
)
TASK_RUNNING_STATUSES: Final[frozenset[str]] = frozenset(
    {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
)
TASK_CANCELLABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
)

BILLING_STATE_VALUES: Final[tuple[str, ...]] = tuple(state.value for state in BillingState)
BILLING_STATE_VALUES_SQL: Final[str] = _sql_quoted_csv(BILLING_STATE_VALUES)
DEFAULT_BILLING_STATE: Final[str] = BillingState.RESERVED.value

TASK_EVENT_TYPES: Final[tuple[str, ...]] = (
    "task.requested",
    "task.started",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "task.expired",
    "billing.captured",
    "billing.released",
)

REDPANDA_TOPIC_TASK_REQUESTED: Final[str] = "tasks.requested"
REDPANDA_TOPIC_TASK_STARTED: Final[str] = "tasks.started"
REDPANDA_TOPIC_TASK_COMPLETED: Final[str] = "tasks.completed"
REDPANDA_TOPIC_TASK_FAILED: Final[str] = "tasks.failed"
REDPANDA_TOPIC_TASK_CANCELLED: Final[str] = "tasks.cancelled"
REDPANDA_TOPIC_TASK_EXPIRED: Final[str] = "tasks.expired"
REDPANDA_TOPIC_BILLING_CAPTURED: Final[str] = "billing.captured"
REDPANDA_TOPIC_BILLING_RELEASED: Final[str] = "billing.released"

RABBITMQ_EXCHANGE_PRELOADED: Final[str] = "preloaded"
RABBITMQ_EXCHANGE_COLDSTART: Final[str] = "coldstart"
RABBITMQ_QUEUE_HOT_SMALL: Final[str] = "hot-small"
RABBITMQ_QUEUE_HOT_MEDIUM: Final[str] = "hot-medium"
RABBITMQ_QUEUE_HOT_LARGE: Final[str] = "hot-large"
RABBITMQ_QUEUE_COLD: Final[str] = "cold"

SEED_ADMIN_CREDITS: Final[int] = 1000
SEED_TEST_USER1_CREDITS: Final[int] = 500
SEED_TEST_USER2_CREDITS: Final[int] = 250

SEED_ADMIN_NAME: Final[str] = "admin"
SEED_ALICE_NAME: Final[str] = "alice"
SEED_BOB_NAME: Final[str] = "bob"
