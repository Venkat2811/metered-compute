"""Shared enums and constants used by API, DB migrations, and workers."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class UserRole(StrEnum):
    """Role values persisted in `users.role`."""

    ADMIN = "admin"
    USER = "user"


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
