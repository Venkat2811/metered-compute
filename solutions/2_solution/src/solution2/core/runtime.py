from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from redis.asyncio import Redis

from solution2.core.settings import AppSettings


@dataclass
class RuntimeState:
    """Runtime resources shared by API handlers."""

    settings: AppSettings
    db_pool: asyncpg.Pool
    redis_client: Redis[str]
    admission_script_sha: str = ""
    decrement_script_sha: str = ""
