"""Background reconciler for orphan/stuck task recovery and credit snapshots."""

from __future__ import annotations

import asyncio
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from functools import partial
from uuid import UUID

import asyncpg
from opentelemetry.trace import SpanKind
from prometheus_client import start_http_server
from redis.asyncio import Redis

from solution1.core.settings import load_settings
from solution1.db.migrate import run_migrations
from solution1.db.repository import (
    bulk_expire_old_terminal_tasks,
    get_task,
    insert_credit_drift_audit,
    insert_credit_transaction,
    list_credit_snapshots,
    list_stuck_running_tasks,
    purge_old_credit_drift_audit,
    purge_old_credit_transactions,
    update_task_failed,
    upsert_credit_snapshot,
)
from solution1.observability.metrics import (
    CREDIT_DRIFT_ABSOLUTE,
    REAPER_DRIFT_ABS,
    REAPER_DRIFT_AUDITS_TOTAL,
    REAPER_REFUNDS_TOTAL,
    REAPER_RETENTION_DELETES_TOTAL,
    SNAPSHOT_FLUSH_DURATION_SECONDS,
    SNAPSHOT_LAST_SUCCESS_UNIXTIME,
)
from solution1.observability.tracing import configure_process_tracing, start_span
from solution1.services.auth import credits_cache_key, idempotency_key
from solution1.services.billing import refund_and_decrement_active
from solution1.utils.logging import configure_logging, get_logger
from solution1.utils.lua_scripts import DECR_ACTIVE_CLAMP_LUA
from solution1.utils.retry import retry_async

logger = get_logger("solution1.workers.reaper")


DB_POOL_ACQUIRE_TIMEOUT_SECONDS = 2.0


@asynccontextmanager
async def _acquire_db_connection(
    pool: asyncpg.Pool,
    *,
    timeout_seconds: float = DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
) -> AsyncIterator[asyncpg.Connection]:
    try:
        async with asyncio.timeout(timeout_seconds):
            async with pool.acquire() as connection:
                yield connection
    except TimeoutError as exc:
        raise TimeoutError(
            f"Timed out waiting {timeout_seconds:.1f}s for PostgreSQL connection from pool"
        ) from exc


async def _process_pending_markers(
    *,
    pool: asyncpg.Pool,
    redis_client: Redis[str],
    decrement_script_sha: str,
    orphan_timeout_seconds: int,
    scan_count: int,
    max_markers_per_cycle: int,
) -> tuple[int, str]:
    """Refund abandoned reservations where Redis pending markers outlive task persistence."""
    recovered = 0

    scanned = 0
    async for marker_key in redis_client.scan_iter(match="pending:*", count=scan_count):
        if scanned >= max_markers_per_cycle:
            break
        scanned += 1
        marker = await redis_client.hgetall(marker_key)
        if not marker:
            continue

        created_at_epoch_text = marker.get("created_at_epoch")
        user_id_text = marker.get("user_id")
        task_id_text = marker.get("task_id")
        cost_text = marker.get("cost")
        idempotency_value = marker.get("idempotency_value")

        if (
            created_at_epoch_text is None
            or user_id_text is None
            or task_id_text is None
            or cost_text is None
            or idempotency_value is None
        ):
            await redis_client.delete(marker_key)
            continue

        age_seconds = int(time.time()) - int(created_at_epoch_text)
        if age_seconds < orphan_timeout_seconds:
            continue

        # Delay cleanup until timeout to avoid refunding in-flight requests
        # that are about to persist.
        task = await get_task(pool, UUID(task_id_text))
        if task is not None:
            await redis_client.delete(marker_key)
            continue

        user_id = UUID(user_id_text)
        cost = int(cost_text)

        async def _refund_orphan_marker(
            *,
            script_sha: str,
            marker_user_id: UUID,
            marker_cost: int,
        ) -> str:
            return await refund_and_decrement_active(
                redis_client=redis_client,
                decrement_script_sha=script_sha,
                user_id=marker_user_id,
                amount=marker_cost,
            )

        refund_orphan_operation = partial(
            _refund_orphan_marker,
            script_sha=decrement_script_sha,
            marker_user_id=user_id,
            marker_cost=cost,
        )
        decrement_script_sha = await retry_async(
            refund_orphan_operation,
            attempts=3,
            base_delay_seconds=0.05,
            max_delay_seconds=0.5,
        )
        await redis_client.delete(idempotency_key(user_id, idempotency_value))
        await redis_client.delete(marker_key)

        REAPER_REFUNDS_TOTAL.labels(reason="orphan_marker").inc()
        recovered += 1

    return recovered, decrement_script_sha


async def _process_stuck_tasks(
    *,
    pool: asyncpg.Pool,
    redis_client: Redis[str],
    decrement_script_sha: str,
    stuck_timeout_seconds: int,
    retry_attempts: int,
    retry_base_delay_seconds: float,
    retry_max_delay_seconds: float,
) -> tuple[int, str]:
    """Fail and refund tasks that exceeded the running timeout budget."""
    stuck_tasks = await list_stuck_running_tasks(pool, timeout_seconds=stuck_timeout_seconds)
    recovered = 0

    for task in stuck_tasks:
        should_refund = False
        try:
            async with (
                _acquire_db_connection(pool) as connection,
                connection.transaction(),
            ):
                failed = await update_task_failed(
                    connection, task_id=task.task_id, error="stuck task timeout"
                )
                if not failed:
                    continue
                await insert_credit_transaction(
                    connection,
                    user_id=task.user_id,
                    task_id=task.task_id,
                    delta=task.cost,
                    reason="stuck_refund",
                )
                should_refund = True
        except Exception as db_exc:
            logger.exception(
                "reaper_stuck_task_refund_error",
                task_id=str(task.task_id),
                error=str(db_exc),
            )
            should_refund = False

        if should_refund:

            async def _refund_stuck_task(
                *,
                script_sha: str,
                task_user_id: UUID,
                task_cost: int,
            ) -> str:
                return await refund_and_decrement_active(
                    redis_client=redis_client,
                    decrement_script_sha=script_sha,
                    user_id=task_user_id,
                    amount=task_cost,
                )

            refund_stuck_operation = partial(
                _refund_stuck_task,
                script_sha=decrement_script_sha,
                task_user_id=task.user_id,
                task_cost=task.cost,
            )
            decrement_script_sha = await retry_async(
                refund_stuck_operation,
                attempts=retry_attempts,
                base_delay_seconds=retry_base_delay_seconds,
                max_delay_seconds=retry_max_delay_seconds,
            )
            REAPER_REFUNDS_TOTAL.labels(reason="stuck_task").inc()
            recovered += 1

    return recovered, decrement_script_sha


async def _flush_credit_snapshots(*, pool: asyncpg.Pool, redis_client: Redis[str]) -> int:
    """Persist dirty Redis credit balances into Postgres snapshot table."""
    started_at = time.perf_counter()
    keys = await redis_client.smembers("credits:dirty")
    flushed = 0

    for credit_key in keys:
        if not credit_key.startswith("credits:"):
            await redis_client.srem("credits:dirty", credit_key)
            continue

        user_id_text = credit_key.split(":", 1)[1]
        balance_text = await redis_client.get(credit_key)
        if balance_text is None:
            await redis_client.srem("credits:dirty", credit_key)
            continue

        await upsert_credit_snapshot(
            pool,
            user_id=UUID(user_id_text),
            balance=int(balance_text),
            snapshot_at=datetime.now(tz=UTC),
        )
        await redis_client.srem("credits:dirty", credit_key)
        flushed += 1

    SNAPSHOT_FLUSH_DURATION_SECONDS.observe(time.perf_counter() - started_at)
    return flushed


async def _run_credit_drift_audit(
    *, pool: asyncpg.Pool, redis_client: Redis[str]
) -> tuple[int, int]:
    """Compare Redis balances with DB snapshots, then audit and reconcile drift."""
    snapshots = await list_credit_snapshots(pool)
    audited = 0
    reconciled = 0

    for user_id, db_balance in snapshots:
        action_taken: str | None = None
        redis_balance_text = await redis_client.get(credits_cache_key(user_id))

        if redis_balance_text is None:
            redis_balance = db_balance
            await redis_client.set(credits_cache_key(user_id), db_balance)
            action_taken = "redis_hydrated_from_snapshot"
        else:
            try:
                redis_balance = int(redis_balance_text)
            except ValueError:
                redis_balance = db_balance
                await redis_client.set(credits_cache_key(user_id), db_balance)
                action_taken = "redis_invalid_reset_from_snapshot"

        drift = redis_balance - db_balance
        REAPER_DRIFT_ABS.observe(abs(drift))
        CREDIT_DRIFT_ABSOLUTE.labels(user_id=str(user_id)).set(abs(drift))

        if drift != 0:
            await upsert_credit_snapshot(
                pool,
                user_id=user_id,
                balance=redis_balance,
                snapshot_at=datetime.now(tz=UTC),
            )
            action_taken = "snapshot_reconciled"
            reconciled += 1

        REAPER_DRIFT_AUDITS_TOTAL.labels(
            result=action_taken if action_taken is not None else "no_drift"
        ).inc()
        await insert_credit_drift_audit(
            pool,
            user_id=user_id,
            redis_balance=redis_balance,
            db_balance=db_balance,
            drift=drift,
            action_taken=action_taken,
        )
        audited += 1

    return audited, reconciled


async def _run_once(
    pool: asyncpg.Pool,
    redis_client: Redis[str],
    decrement_script_sha: str,
) -> str:
    """Execute a single reaper cycle and return current decrement-script SHA."""
    settings = load_settings()
    with start_span(
        tracer_name="solution1.reaper",
        span_name="reaper.cycle",
        kind=SpanKind.INTERNAL,
        attributes={"reaper.interval_seconds": getattr(settings, "reaper_interval_seconds", 0.0)},
    ):
        orphan_refunds, decrement_script_sha = await _process_pending_markers(
            pool=pool,
            redis_client=redis_client,
            decrement_script_sha=decrement_script_sha,
            orphan_timeout_seconds=settings.orphan_marker_timeout_seconds,
            scan_count=int(getattr(settings, "reaper_pending_scan_count", 100)),
            max_markers_per_cycle=int(getattr(settings, "reaper_pending_max_per_cycle", 500)),
        )
        stuck_refunds, decrement_script_sha = await _process_stuck_tasks(
            pool=pool,
            redis_client=redis_client,
            decrement_script_sha=decrement_script_sha,
            stuck_timeout_seconds=settings.task_stuck_timeout_seconds,
            retry_attempts=int(getattr(settings, "redis_retry_attempts", 3)),
            retry_base_delay_seconds=float(
                getattr(settings, "redis_retry_base_delay_seconds", 0.05)
            ),
            retry_max_delay_seconds=float(getattr(settings, "redis_retry_max_delay_seconds", 0.5)),
        )
        snapshots = await _flush_credit_snapshots(pool=pool, redis_client=redis_client)
        SNAPSHOT_LAST_SUCCESS_UNIXTIME.set(time.time())
        drift_audits, drift_reconciled = await _run_credit_drift_audit(
            pool=pool, redis_client=redis_client
        )
        expired = await bulk_expire_old_terminal_tasks(
            pool,
            older_than_seconds=settings.task_result_ttl_seconds,
        )
        purged_transactions = 0
        if settings.reaper_credit_transaction_retention_seconds > 0:
            purged_transactions = await purge_old_credit_transactions(
                pool,
                older_than_seconds=settings.reaper_credit_transaction_retention_seconds,
                batch_size=settings.reaper_retention_batch_size,
            )

        purged_drift_audit = 0
        if settings.reaper_credit_drift_audit_retention_seconds > 0:
            purged_drift_audit = await purge_old_credit_drift_audit(
                pool,
                older_than_seconds=settings.reaper_credit_drift_audit_retention_seconds,
                batch_size=settings.reaper_retention_batch_size,
            )
        if purged_transactions:
            REAPER_RETENTION_DELETES_TOTAL.labels(table="credit_transactions").inc(
                purged_transactions
            )
        if purged_drift_audit:
            REAPER_RETENTION_DELETES_TOTAL.labels(table="credit_drift_audit").inc(
                purged_drift_audit
            )

        logger.info(
            "reaper_cycle",
            orphan_refunds=orphan_refunds,
            stuck_refunds=stuck_refunds,
            snapshots=snapshots,
            drift_audits=drift_audits,
            drift_reconciled=drift_reconciled,
            expired=expired,
            purged_transactions=purged_transactions,
            purged_drift_audit=purged_drift_audit,
        )
        return decrement_script_sha


async def main_async() -> None:
    """Run reaper loop until SIGINT/SIGTERM."""
    configure_logging()
    settings = load_settings()
    base_service_name = str(getattr(settings, "app_name", "mc-solution1"))
    configure_process_tracing(
        settings=settings,
        service_name=f"{base_service_name}-reaper",
    )

    await run_migrations(str(settings.postgres_dsn))

    pool = await asyncpg.create_pool(
        dsn=str(settings.postgres_dsn),
        min_size=settings.db_pool_min_size,
        max_size=max(2, min(settings.db_pool_max_size, 8)),
        command_timeout=settings.db_pool_command_timeout_seconds,
        max_inactive_connection_lifetime=settings.db_pool_max_inactive_connection_lifetime_seconds,
        server_settings={
            "statement_timeout": f"{settings.db_statement_timeout_ms}ms",
            "idle_in_transaction_session_timeout": (
                f"{settings.db_idle_in_transaction_timeout_ms}ms"
            ),
        },
    )
    redis_client = Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        max_connections=50,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
    )
    decrement_script_sha = str(await redis_client.script_load(DECR_ACTIVE_CLAMP_LUA))
    stop_event = asyncio.Event()

    try:
        start_http_server(settings.reaper_metrics_port)
    except OSError:
        logger.warning("reaper_metrics_port_in_use", port=settings.reaper_metrics_port)

    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("reaper_shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        while not stop_event.is_set():
            try:
                decrement_script_sha = await _run_once(pool, redis_client, decrement_script_sha)
            except Exception as exc:
                logger.exception("reaper_cycle_failed", error=str(exc))
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=settings.reaper_error_backoff_seconds,
                    )
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=settings.reaper_interval_seconds)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        await redis_client.close()
        await pool.close()
        logger.info("reaper_shutdown_complete")


def main() -> None:
    """Sync entrypoint for container execution."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
