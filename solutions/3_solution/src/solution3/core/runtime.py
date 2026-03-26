from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from redis.asyncio import Redis

from solution3.core.settings import AppSettings


@dataclass(slots=True)
class RuntimeState:
    """Shared state objects for solution3 runtime services."""

    settings: AppSettings
    db_pool: asyncpg.Pool | None = None
    redis_client: Redis[str] | None = None
    started: bool = False
