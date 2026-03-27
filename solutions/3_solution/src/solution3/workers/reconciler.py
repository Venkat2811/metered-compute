from __future__ import annotations

import argparse
import asyncio
import signal
from typing import Protocol, cast

import asyncpg
import tigerbeetle as tb
from redis.asyncio import Redis

from solution3.constants import BillingState, TaskStatus
from solution3.core.settings import AppSettings, load_settings
from solution3.db.repository import (
    align_stale_reserved_task_terminal_state,
    expire_stale_reserved_task,
    list_stale_reserved_tasks,
)
from solution3.models.domain import ReconciledTaskState
from solution3.services.billing import (
    PendingTransferState,
    TigerBeetleBilling,
    resolve_tigerbeetle_addresses,
)
from solution3.utils.logging import configure_logging, get_logger

logger = get_logger("solution3.workers.reconciler")


class ReconcilerRedis(Protocol):
    async def ping(self) -> bool: ...

    async def close(self) -> None: ...

    async def hset(self, key: str, mapping: dict[str, str]) -> None: ...

    async def expire(self, key: str, seconds: int) -> None: ...

    async def decr(self, key: str) -> int: ...

    async def set(self, key: str, value: int) -> None: ...


class ReconcilerBilling(Protocol):
    def get_pending_transfer_state(
        self, *, pending_transfer_id: object
    ) -> PendingTransferState: ...


def _task_state_key(task_id: object) -> str:
    return f"task:{task_id}"


def _active_counter_key(user_id: object) -> str:
    return f"active:{user_id}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 reconciler")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--stale-after-seconds", type=int, default=720)
    parser.add_argument("--result-ttl-seconds", type=int, default=86_400)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    def _stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_args: stop_event.set())


async def _decrement_active_counter(redis_client: ReconcilerRedis, *, user_id: object) -> None:
    key = _active_counter_key(user_id)
    value = await redis_client.decr(key)
    if value < 0:
        await redis_client.set(key, 0)


async def _cache_reconciled_state(
    *,
    redis_client: ReconcilerRedis | None,
    state: ReconciledTaskState,
    result_ttl_seconds: int,
) -> None:
    if redis_client is None:
        return
    await _decrement_active_counter(redis_client, user_id=state.user_id)
    await redis_client.hset(
        _task_state_key(state.task_id),
        mapping={
            "user_id": str(state.user_id),
            "status": state.status.value,
            "billing_state": state.billing_state.value,
            "model_class": state.model_class.value,
        },
    )
    await redis_client.expire(_task_state_key(state.task_id), result_ttl_seconds)


def _terminal_alignment_target(
    task_status: TaskStatus,
    transfer_state: PendingTransferState,
) -> tuple[TaskStatus, BillingState, str] | None:
    if transfer_state == PendingTransferState.POSTED:
        return TaskStatus.COMPLETED, BillingState.CAPTURED, "TB_CAPTURED"
    if transfer_state == PendingTransferState.VOIDED:
        status = TaskStatus.CANCELLED if task_status == TaskStatus.PENDING else TaskStatus.FAILED
        return status, BillingState.RELEASED, "TB_VOIDED"
    return None


def _build_billing(settings: AppSettings) -> tuple[tb.ClientSync, TigerBeetleBilling]:
    endpoint = resolve_tigerbeetle_addresses(settings.tigerbeetle_endpoint)
    client = tb.ClientSync(
        cluster_id=settings.tigerbeetle_cluster_id,
        replica_addresses=endpoint,
    )
    return client, TigerBeetleBilling(
        client=client,
        ledger_id=settings.tigerbeetle_ledger_id,
        revenue_account_id=settings.tigerbeetle_revenue_account_id,
        escrow_account_id=settings.tigerbeetle_escrow_account_id,
        pending_timeout_seconds=settings.tigerbeetle_pending_transfer_timeout_seconds,
    )


async def reconcile_stale_reserved_tasks(
    *,
    db_pool: asyncpg.Pool,
    redis_client: ReconcilerRedis | None,
    billing: ReconcilerBilling,
    stale_after_seconds: int,
    result_ttl_seconds: int,
) -> int:
    stale_tasks = await list_stale_reserved_tasks(
        db_pool,
        stale_after_seconds=stale_after_seconds,
    )
    resolved = 0
    for task in stale_tasks:
        transfer_state = await asyncio.to_thread(
            billing.get_pending_transfer_state,
            pending_transfer_id=task.tb_pending_transfer_id,
        )
        if transfer_state == PendingTransferState.PENDING:
            continue
        if transfer_state == PendingTransferState.ABSENT:
            reconciled = await expire_stale_reserved_task(
                db_pool,
                task_id=task.task_id,
                tb_pending_transfer_id=task.tb_pending_transfer_id,
                stale_after_seconds=stale_after_seconds,
            )
        else:
            target = _terminal_alignment_target(task.status, transfer_state)
            if target is None:
                continue
            status, billing_state, resolution = target
            reconciled = await align_stale_reserved_task_terminal_state(
                db_pool,
                task=task,
                status=status,
                billing_state=billing_state,
                resolution=resolution,
                stale_after_seconds=stale_after_seconds,
            )
        if reconciled is not None:
            await _cache_reconciled_state(
                redis_client=redis_client,
                state=reconciled,
                result_ttl_seconds=result_ttl_seconds,
            )
            resolved += 1
    return resolved


async def _main_async(
    *,
    once: bool,
    stale_after_seconds: int,
    interval_seconds: float = 30.0,
    result_ttl_seconds: int = 86_400,
) -> None:
    settings = load_settings()
    db_pool = await asyncpg.create_pool(dsn=str(settings.postgres_dsn))
    redis_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    tb_client, billing = _build_billing(settings)
    await redis_client.ping()
    stop_event = asyncio.Event()
    _install_stop_handlers(stop_event)

    try:
        while not stop_event.is_set():
            resolved = await reconcile_stale_reserved_tasks(
                db_pool=db_pool,
                redis_client=cast(ReconcilerRedis, redis_client),
                billing=cast(ReconcilerBilling, billing),
                stale_after_seconds=stale_after_seconds,
                result_ttl_seconds=result_ttl_seconds,
            )
            if resolved > 0:
                logger.info("reconciler_stale_resolved", count=resolved)
            if once:
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
    finally:
        tb_client.close()
        await redis_client.close()
        await db_pool.close()


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    asyncio.run(
        _main_async(
            once=bool(args.once),
            stale_after_seconds=max(int(args.stale_after_seconds), 1),
            interval_seconds=max(float(args.interval), 0.1),
            result_ttl_seconds=max(int(args.result_ttl_seconds), 1),
        )
    )


if __name__ == "__main__":
    main()
