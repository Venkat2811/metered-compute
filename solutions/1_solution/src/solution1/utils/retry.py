"""Retry helpers with bounded exponential backoff."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from random import uniform


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    backoff_multiplier: float = 2.0,
) -> T:
    """Run an async operation with bounded exponential retry/backoff."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if base_delay_seconds < 0:
        raise ValueError("base_delay_seconds must be >= 0")
    if max_delay_seconds < 0:
        raise ValueError("max_delay_seconds must be >= 0")
    if backoff_multiplier < 1.0:
        raise ValueError("backoff_multiplier must be >= 1.0")

    attempt = 1
    delay_seconds = base_delay_seconds
    while True:
        try:
            return await operation()
        except Exception:
            if attempt >= attempts:
                raise
            if delay_seconds > 0:
                jitter = uniform(0.5, 1.5)  # nosec: B311
                await asyncio.sleep(min(delay_seconds * jitter, max_delay_seconds))
            delay_seconds = min(max_delay_seconds, delay_seconds * backoff_multiplier)
            attempt += 1
