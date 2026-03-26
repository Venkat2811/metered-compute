from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

import asyncpg

from solution3.constants import BillingState, ModelClass, TaskStatus
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


class WarmRegistry(Protocol):
    async def sadd(self, key: str, value: str) -> int: ...


def _active_counter_key(user_id: UUID) -> str:
    return f"active:{user_id}"


def _task_state_key(task_id: UUID) -> str:
    return f"task:{task_id}"


def _warm_registry_key(model_class: ModelClass) -> str:
    return f"warm:{model_class.value}"


def _inference_seconds(model_class: ModelClass) -> float:
    if model_class == ModelClass.SMALL:
        return 2.0
    if model_class == ModelClass.MEDIUM:
        return 4.0
    return 6.0


class WorkerModelRuntime:
    def __init__(
        self,
        *,
        worker_id: str,
        redis_client: WarmRegistry | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.redis_client = redis_client
        self.warm_model: ModelClass | None = None

    async def go_warm(self, model_class: ModelClass) -> None:
        self.warm_model = model_class
        if self.redis_client is not None:
            await self.redis_client.sadd(_warm_registry_key(model_class), self.worker_id)

    async def execute(self, task: TaskCommand) -> dict[str, int]:
        if self.warm_model != task.model_class:
            await asyncio.sleep(3.0)
            await self.go_warm(task.model_class)
        await asyncio.sleep(_inference_seconds(task.model_class))
        return {"sum": task.x + task.y}


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
