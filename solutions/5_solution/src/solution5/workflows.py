"""Restate durable workflows — replaces outbox + relay + watchdog + compensation.

ARCHITECTURE: Control plane vs data plane separation.

Restate manages the task lifecycle (control plane): mark running, capture credits,
store result, update cache. Each of these steps is journaled or idempotent.

The actual inference/compute runs OUTSIDE Restate (data plane).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import asyncpg
import redis.asyncio as aioredis
import restate
import structlog

from solution5 import cache, metrics, repository
from solution5.billing import Billing
from solution5.settings import Settings
from solution5.workers.compute_gateway import ComputeError, request_compute_sync

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


def _settings() -> Settings:
    return _state["settings"]  # type: ignore[no-any-return]


task_service = restate.Service("TaskService")


def _build_replayable_action(action: Callable[..., Any], *args: Any, **kwargs: Any) -> Callable[[], Any]:
    """Build a non-inlineable callable that Restate can execute durably."""

    if inspect.iscoroutinefunction(action):

        async def _async_action() -> Any:
            return await action(*args, **kwargs)

        return _async_action

    def _sync_action() -> Any:
        return action(*args, **kwargs)

    return _sync_action


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
    task_settings = _settings()

    async def _run_replay_compatible(name: str, action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await ctx.run(name, _build_replayable_action(action, *args, **kwargs))

    # ── Control: mark running (guarded — safe to replay) ──
    started = await _run_replay_compatible(
        "mark_running",
        repository.update_task_status_if_match,
        pool,
        task_id,
        "RUNNING",
        expected_status="PENDING",
    )
    if not started:
        current_status = await _run_replay_compatible(
            "read_status_after_start",
            repository.get_task_status,
            pool,
            task_id,
        )
        if current_status in {"RUNNING", "COMPLETED", "FAILED", "CANCELLED"}:
            # Replay or duplicate event. If already terminal, return terminal result.
            return {"status": current_status}
        # Could be missing row (corrupt request) or unexpected transition.
        log.warning("workflow_start_transition_failed", task_id=task_id, status=current_status)
        return {"status": "REJECTED"}

    # ── Data plane: external compute via dedicated worker ──
    # Restate keeps the lifecycle control plane durable; compute is separated.
    try:
        result: dict[str, int] = await _run_replay_compatible(
            "compute",
            request_compute_sync,
            task_id=task_id,
            x=x,
            y=y,
            base_url=task_settings.compute_worker_url,
            timeout_seconds=task_settings.compute_timeout_seconds,
            retry_attempts=task_settings.compute_retry_attempts,
        )
        await _run_replay_compatible(
            "compute_metric_ok",
            metrics.COMPUTE_REQUESTS.labels(result="ok").inc,
        )
    except Exception as error:
        await _run_replay_compatible(
            "compute_metric_error",
            metrics.COMPUTE_REQUESTS.labels(result="error").inc,
        )
        if isinstance(error, ComputeError):
            await _run_replay_compatible(
                "compute_timeout_metric",
                metrics.TASK_TIMEOUT.inc,
            )
        updated = await _run_replay_compatible(
            "mark_failed_after_compute",
            repository.update_task_status_if_match,
            pool,
            task_id,
            "FAILED",
            expected_status="RUNNING",
        )
        await _run_replay_compatible(
            "task_failed_after_compute",
            metrics.TASK_FAILED.inc,
        )
        if updated:
            await _run_replay_compatible(
                "invalidate_task_after_compute_failure",
                cache.invalidate_task,
                redis_conn,
                task_id,
            )
            return {"status": "FAILED", "reason": "compute_failed"}

        current_status = await _run_replay_compatible(
            "read_status_after_compute_failure",
            repository.get_task_status,
            pool,
            task_id,
        )
        if current_status in {"COMPLETED", "CANCELLED", "FAILED"}:
            return {"status": current_status}
        log.warning("workflow_compute_failed", task_id=task_id, error=str(error))
        return {"status": "REJECTED"}

    # ── Control: capture credits in TigerBeetle (journaled — money operation) ──
    captured: bool = await _run_replay_compatible(
        "capture_credits",
        billing.capture_credits,
        tb_transfer_id,
    )

    if not captured:
        await _run_replay_compatible(
            "mark_failed_after_capture",
            repository.update_task_status_if_match,
            pool,
            task_id,
            "FAILED",
            expected_status="RUNNING",
        )
        if not billing.release_credits(tb_transfer_id):
            log.warning("workflow_credit_release_failed", task_id=task_id)
            # Task remains FAILED; credits may auto-release on TB timeout.
        else:
            await _run_replay_compatible(
                "invalidate_task_after_capture_release",
                cache.invalidate_task,
                redis_conn,
                task_id,
            )
        await _run_replay_compatible(
            "task_failed_after_capture",
            metrics.TASK_FAILED.inc,
        )
        log.warning("workflow_capture_failed", task_id=task_id)
        return {"status": "FAILED", "reason": "credit_capture_failed"}

    await _run_replay_compatible(
        "task_completed_metric",
        metrics.TASK_COMPLETED.inc,
    )

    # ── Control: store result + update cache (idempotent) ──
    updated = await _run_replay_compatible(
        "mark_completed",
        repository.update_task_status_if_match,
        pool,
        task_id,
        "COMPLETED",
        result=result,
        expected_status="RUNNING",
    )
    if not updated:
        current_status = await _run_replay_compatible(
            "read_status_after_completion",
            repository.get_task_status,
            pool,
            task_id,
        )
        if current_status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return {"status": current_status}
        return {"status": "REJECTED"}

    await _run_replay_compatible(
        "cache_completed_task",
        cache.cache_task,
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
