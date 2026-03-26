from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from redis.asyncio import Redis

from solution3.core.settings import AppSettings
from solution3.services.billing import TigerBeetleBilling


@dataclass(slots=True)
class RuntimeState:
    """Shared state objects for solution3 runtime services."""

    settings: AppSettings
    db_pool: asyncpg.Pool | None = None
    redis_client: Redis[str] | None = None
    billing_client: TigerBeetleBilling | None = None
    started: bool = False
