"""Restate durable workflows — replaces outbox + relay + watchdog + compensation.

ARCHITECTURE: Control plane vs data plane separation.

Restate manages the task lifecycle (control plane): mark running, capture credits,
store result, update cache. Each of these steps is journaled or idempotent.

The actual inference/compute runs OUTSIDE Restate (data plane). In this demo the
compute is trivial (x+y), so it runs inline. In production, the handler would
dispatch to a GPU worker pool (via Redis queue, gRPC, etc.) and await the result
using ctx.run() or ctx.awakeable(). This keeps Restate handlers lightweight and
avoids coupling durable execution to long-running inference workloads.
"""

from __future__ import annotations

import time
from typing import Any

import asyncpg
import redis.asyncio as aioredis
import restate
import structlog

from solution5 import cache, metrics, repository
from solution5.billing import Billing

log = structlog.get_logger()

# Shared state — populated by app.py lifespan before any handler is invoked.
# Typed accessors below provide safe, checked access.
_state: dict[str, Any] = {}


def _pg_pool() -> asyncpg.Pool:
    return _state["pg_pool"]


def _billing() -> Billing:
    return _state["billing"]  # type: ignore[no-any-return]


def _redis() -> aioredis.Redis:
    return _state["redis"]  # type: ignore[no-any-return]


task_service = restate.Service("TaskService")


@task_service.handler()
async def execute_task(ctx: restate.Context, request: dict[str, Any]) -> dict[str, Any]:
    """Durable task lifecycle orchestrator (control plane).

    Journaled steps survive process crashes. Restate replays from last completed step.
    """
    task_id: str = str(request["task_id"])
    tb_transfer_id = int(str(request["tb_transfer_id"]), 16)
    x: int = int(request["x"])
    y: int = int(request["y"])

    pool = _pg_pool()
    billing = _billing()
    redis_conn = _redis()

    # ── Control: mark running (idempotent — safe to replay) ──
    await repository.update_task_status(pool, task_id, "RUNNING")

    # ── Data plane: compute result ──
    # In production this would dispatch to a GPU worker pool and await the result.
    # Here it's inline because the demo compute is trivial.
    result: dict[str, int] = await ctx.run("compute", lambda: _compute(x, y))

    # ── Control: capture credits in TigerBeetle (journaled — money operation) ──
    captured: bool = await ctx.run("capture_credits", lambda: billing.capture_credits(tb_transfer_id))

    if not captured:
        await repository.update_task_status(pool, task_id, "FAILED")
        metrics.TASK_FAILED.inc()
        log.warning("workflow_capture_failed", task_id=task_id)
        return {"status": "FAILED", "reason": "credit_capture_failed"}

    metrics.TASK_COMPLETED.inc()

    # ── Control: store result + update cache (idempotent) ──
    await repository.update_task_status(pool, task_id, "COMPLETED", result=result)

    await cache.cache_task(
        redis_conn,
        task_id,
        {
            "task_id": task_id,
            "status": "COMPLETED",
            "result": str(result),
        },
    )

    log.info("workflow_completed", task_id=task_id)
    return {"status": "COMPLETED", "result": result}


def _compute(x: int, y: int) -> dict[str, int]:
    """Simulate inference — toy compute for demo.

    Production replacement: dispatch to GPU worker pool via Redis queue or gRPC,
    await result. Restate journals the result so crashes after compute don't
    re-run inference.
    """
    time.sleep(0.5)
    return {"sum": x + y, "product": x * y}
