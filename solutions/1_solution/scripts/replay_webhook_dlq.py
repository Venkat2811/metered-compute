#!/usr/bin/env python3
"""Replay webhook dead-letter events back into the pending webhook queue."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from typing import Any

from redis.asyncio import Redis

from solution1.core.settings import load_settings
from solution1.services.webhooks import parse_webhook_event, serialize_webhook_event


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100, help="Maximum DLQ events to replay")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect replay candidates without mutating Redis",
    )
    return parser.parse_args()


async def _replay_once(
    *,
    redis_client: Redis[str],
    queue_key: str,
    dlq_key: str,
    dry_run: bool,
) -> str:
    raw = await redis_client.rpop(dlq_key)
    if raw is None:
        return "empty"
    payload = str(raw)
    event = parse_webhook_event(payload)
    if event is None:
        if dry_run:
            await redis_client.rpush(dlq_key, payload)
        return "invalid"
    if dry_run:
        await redis_client.rpush(dlq_key, payload)
        return "candidate"

    replay_event = replace(event, attempt=0, last_error=None)
    await redis_client.lpush(queue_key, serialize_webhook_event(replay_event))
    return "replayed"


async def main_async() -> int:
    args = _parse_args()
    settings = load_settings()
    queue_key = str(getattr(settings, "webhook_queue_key", "webhook:queue"))
    dlq_key = str(getattr(settings, "webhook_dlq_key", "webhook:dlq"))
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    summary: dict[str, Any] = {
        "limit": args.limit,
        "dry_run": bool(args.dry_run),
        "replayed": 0,
        "invalid": 0,
        "candidate": 0,
    }
    try:
        for _ in range(max(0, args.limit)):
            result = await _replay_once(
                redis_client=redis_client,
                queue_key=queue_key,
                dlq_key=dlq_key,
                dry_run=args.dry_run,
            )
            if result == "empty":
                break
            if result == "replayed":
                summary["replayed"] += 1
            elif result == "invalid":
                summary["invalid"] += 1
            elif result == "candidate":
                summary["candidate"] += 1
    finally:
        await redis_client.close()

    print(json.dumps(summary, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
