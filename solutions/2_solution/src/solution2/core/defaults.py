"""Lazy-access defaults used in tests and local scripts.

This module intentionally avoids evaluating settings at import time.
Values are resolved only when a `DEFAULT_*` attribute is accessed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from solution2.core.settings import AppSettings, load_settings

if TYPE_CHECKING:
    DEFAULT_ADMIN_API_KEY: str
    DEFAULT_ALICE_API_KEY: str
    DEFAULT_BOB_API_KEY: str
    DEFAULT_TASK_COST: int
    DEFAULT_MAX_CONCURRENT: int
    DEFAULT_AUTH_CACHE_TTL_SECONDS: int
    DEFAULT_IDEMPOTENCY_TTL_SECONDS: int
    DEFAULT_TASK_RESULT_TTL_SECONDS: int
    DEFAULT_PENDING_MARKER_TTL_SECONDS: int
    DEFAULT_REDIS_TASK_STATE_TTL_SECONDS: int


@lru_cache(maxsize=1)
def _settings() -> AppSettings:
    return load_settings()


@lru_cache(maxsize=1)
def _default_values() -> dict[str, str | int]:
    settings = _settings()
    return {
        "DEFAULT_ADMIN_API_KEY": settings.admin_api_key,
        "DEFAULT_ALICE_API_KEY": settings.alice_api_key,
        "DEFAULT_BOB_API_KEY": settings.bob_api_key,
        "DEFAULT_TASK_COST": settings.task_cost,
        "DEFAULT_MAX_CONCURRENT": settings.max_concurrent,
        "DEFAULT_AUTH_CACHE_TTL_SECONDS": settings.auth_cache_ttl_seconds,
        "DEFAULT_IDEMPOTENCY_TTL_SECONDS": settings.idempotency_ttl_seconds,
        "DEFAULT_TASK_RESULT_TTL_SECONDS": settings.task_result_ttl_seconds,
        "DEFAULT_PENDING_MARKER_TTL_SECONDS": settings.pending_marker_ttl_seconds,
        "DEFAULT_REDIS_TASK_STATE_TTL_SECONDS": settings.redis_task_state_ttl_seconds,
    }


def __getattr__(name: str) -> str | int:
    values = _default_values()
    if name in values:
        return values[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals().keys(), *_default_values().keys()])


__all__ = [
    "DEFAULT_ADMIN_API_KEY",
    "DEFAULT_ALICE_API_KEY",
    "DEFAULT_AUTH_CACHE_TTL_SECONDS",
    "DEFAULT_BOB_API_KEY",
    "DEFAULT_IDEMPOTENCY_TTL_SECONDS",
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_PENDING_MARKER_TTL_SECONDS",
    "DEFAULT_REDIS_TASK_STATE_TTL_SECONDS",
    "DEFAULT_TASK_COST",
    "DEFAULT_TASK_RESULT_TTL_SECONDS",
]
