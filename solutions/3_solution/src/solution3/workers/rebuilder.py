from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import uuid4

import asyncpg
from redis.asyncio import Redis

from solution3.core.settings import load_settings
from solution3.db.repository import (
    rebuild_task_query_view_from_commands,
    reset_projection_state,
)
from solution3.utils.logging import configure_logging, get_logger
from solution3.workers.projector import (
    ProjectorConsumer,
    ProjectorRedis,
    build_redpanda_consumer,
    project_message,
)

logger = get_logger("solution3.workers.rebuilder")

_PROJECTOR_CONSUMER_NAMES = ("projector", "projector-rebuild")
_PROJECTOR_NAMES = ("projector", "projector-rebuild")


@dataclass(frozen=True, slots=True)
class RebuildResult:
    records_processed: int
    cache_keys_deleted: int


class RebuilderRedis(Protocol):
    async def ping(self) -> bool: ...

    async def delete(self, *keys: str) -> int: ...

    async def close(self) -> None: ...

    def scan_iter(self, *, match: str) -> AsyncIterator[str]: ...


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 projector rebuild tool")
    parser.add_argument(
        "--from-beginning",
        action="store_true",
        help="replay the Redpanda event log from the earliest offset",
    )
    parser.add_argument("--poll-timeout-ms", type=int, default=1000)
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--result-ttl-seconds", type=int, default=86_400)
    parser.add_argument(
        "--max-empty-polls",
        type=int,
        default=2,
        help="stop replay after this many consecutive empty polls",
    )
    return parser.parse_args()


async def clear_task_cache(redis_client: RebuilderRedis | None) -> int:
    if redis_client is None:
        return 0

    deleted = 0
    batch: list[str] = []
    async for key in redis_client.scan_iter(match="task:*"):
        batch.append(key)
        if len(batch) >= 500:
            deleted += await redis_client.delete(*batch)
            batch.clear()
    if batch:
        deleted += await redis_client.delete(*batch)
    return deleted


async def rebuild_from_sql(
    *,
    db_pool: asyncpg.Pool,
    redis_client: RebuilderRedis | None,
) -> RebuildResult:
    deleted = await clear_task_cache(redis_client)
    await reset_projection_state(
        db_pool,
        consumer_names=_PROJECTOR_CONSUMER_NAMES,
        projector_names=_PROJECTOR_NAMES,
    )
    rebuilt = await rebuild_task_query_view_from_commands(db_pool)
    return RebuildResult(records_processed=rebuilt, cache_keys_deleted=deleted)


async def rebuild_from_events(
    *,
    db_pool: asyncpg.Pool,
    redis_client: RebuilderRedis | None,
    consumer: ProjectorConsumer,
    poll_timeout_ms: int,
    max_records: int,
    task_result_ttl_seconds: int,
    max_empty_polls: int,
) -> RebuildResult:
    deleted = await clear_task_cache(redis_client)
    await reset_projection_state(
        db_pool,
        consumer_names=_PROJECTOR_CONSUMER_NAMES,
        projector_names=_PROJECTOR_NAMES,
    )

    processed = 0
    empty_polls = 0
    while empty_polls < max_empty_polls:
        polled = consumer.poll(timeout_ms=poll_timeout_ms, max_records=max_records)
        messages = [message for batch in polled.values() for message in batch]
        if not messages:
            empty_polls += 1
            continue

        empty_polls = 0
        for message in messages:
            projected = await project_message(
                db_pool=db_pool,
                redis_client=cast(ProjectorRedis | None, redis_client),
                consumer_name="projector",
                projector_name="projector",
                message=message,
                task_result_ttl_seconds=task_result_ttl_seconds,
            )
            if projected:
                processed += 1
        consumer.commit()

    return RebuildResult(records_processed=processed, cache_keys_deleted=deleted)


async def _main_async(
    *,
    from_beginning: bool,
    max_empty_polls: int,
    poll_timeout_ms: int = 1000,
    max_records: int = 100,
    task_result_ttl_seconds: int = 86_400,
) -> None:
    settings = load_settings()
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await redis_client.ping()
    rebuilder_redis = cast(RebuilderRedis, redis_client)
    consumer: ProjectorConsumer | None = None

    try:
        if from_beginning:
            consumer = build_redpanda_consumer(
                settings,
                group_id=f"solution3-projector-rebuild-{uuid4()}",
                auto_offset_reset="earliest",
            )
            result = await rebuild_from_events(
                db_pool=db_pool,
                redis_client=rebuilder_redis,
                consumer=consumer,
                poll_timeout_ms=poll_timeout_ms,
                max_records=max_records,
                task_result_ttl_seconds=task_result_ttl_seconds,
                max_empty_polls=max_empty_polls,
            )
            logger.info(
                "projection_rebuild_completed",
                strategy="events",
                records_processed=result.records_processed,
                cache_keys_deleted=result.cache_keys_deleted,
            )
            return

        result = await rebuild_from_sql(
            db_pool=db_pool,
            redis_client=rebuilder_redis,
        )
        logger.info(
            "projection_rebuild_completed",
            strategy="sql",
            records_processed=result.records_processed,
            cache_keys_deleted=result.cache_keys_deleted,
        )
    finally:
        if consumer is not None:
            consumer.close()
        await redis_client.close()
        await db_pool.close()


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    asyncio.run(
        _main_async(
            from_beginning=bool(args.from_beginning),
            max_empty_polls=max(int(args.max_empty_polls), 1),
            poll_timeout_ms=max(int(getattr(args, "poll_timeout_ms", 1000)), 1),
            max_records=max(int(getattr(args, "max_records", 100)), 1),
            task_result_ttl_seconds=max(int(getattr(args, "result_ttl_seconds", 86_400)), 1),
        )
    )


if __name__ == "__main__":
    main()
