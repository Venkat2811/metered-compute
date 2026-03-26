from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

import asyncpg

from solution3.constants import BillingState, TaskStatus
from solution3.db.repository import finalize_task_command, update_task_running
from solution3.models.domain import TaskCommand
from solution3.workers._bootstrap_worker import run_worker


class WorkerRedis(Protocol):
    async def decr(self, key: str) -> int: ...

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None: ...

    async def expire(self, key: str, seconds: int) -> None: ...


class WorkerBilling(Protocol):
    def post_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool: ...

    def void_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool: ...


def _active_counter_key(user_id: UUID) -> str:
    return f"active:{user_id}"


def _task_state_key(task_id: UUID) -> str:
    return f"task:{task_id}"


async def mark_task_running(
    *,
    db_pool: asyncpg.Pool,
    redis_client: WorkerRedis | None,
    task: TaskCommand,
    result_ttl_seconds: int,
) -> bool:
    updated = await update_task_running(db_pool, task_id=task.task_id)
    if not updated:
        return False
    if redis_client is not None:
        await redis_client.hset(
            _task_state_key(task.task_id),
            mapping={
                "user_id": str(task.user_id),
                "status": TaskStatus.RUNNING.value,
                "billing_state": task.billing_state.value,
                "model_class": task.model_class.value,
            },
        )
        await redis_client.expire(_task_state_key(task.task_id), result_ttl_seconds)
    return True


async def handle_task_completion(
    *,
    db_pool: asyncpg.Pool,
    redis_client: WorkerRedis | None,
    billing: WorkerBilling,
    task: TaskCommand,
    success: bool,
    result_ttl_seconds: int,
    result: dict[str, int] | None = None,
    error: str | None = None,
) -> bool:
    if success:
        tb_ok = billing.post_pending_transfer(pending_transfer_id=task.tb_pending_transfer_id)
        status = TaskStatus.COMPLETED
        billing_state = BillingState.CAPTURED
    else:
        tb_ok = billing.void_pending_transfer(pending_transfer_id=task.tb_pending_transfer_id)
        status = TaskStatus.FAILED
        billing_state = BillingState.RELEASED

    if not tb_ok:
        return False

    finalized = await finalize_task_command(
        db_pool,
        task_id=task.task_id,
        user_id=task.user_id,
        status=status,
        billing_state=billing_state,
        cost=task.cost,
        result=result,
        error=error,
    )
    if not finalized:
        return False

    if redis_client is not None:
        await redis_client.decr(_active_counter_key(task.user_id))
        mapping = {
            "user_id": str(task.user_id),
            "status": status.value,
            "billing_state": billing_state.value,
            "model_class": task.model_class.value,
        }
        if result is not None:
            mapping["result"] = str(result)
        if error is not None:
            mapping["error"] = error
        await redis_client.hset(_task_state_key(task.task_id), mapping=mapping)
        await redis_client.expire(_task_state_key(task.task_id), result_ttl_seconds)
    return True


def main() -> None:
    run_worker(name="solution3_worker")


if __name__ == "__main__":
    main()
