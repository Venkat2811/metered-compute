from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID

import asyncpg
from uuid6 import uuid7

from solution2.constants import (
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
    compute_routing_key,
    resolve_queue,
    runtime_seconds_for_model,
    task_cost_for_model,
)
from solution2.db.repository import (
    add_user_credits,
    capture_reservation,
    count_active_reservations,
    create_outbox_event,
    create_reservation,
    create_task_command,
    get_credit_reservation,
    get_task_command_by_idempotency,
    insert_credit_transaction,
    lock_user_for_admission,
    release_reservation,
    reserve_user_credits,
    update_task_command_completed,
    update_task_command_failed,
    update_task_command_running,
    update_task_command_timed_out,
    upsert_task_query_view,
)
from solution2.models.domain import AdmissionDecision
from solution2.observability.metrics import CREDIT_LUA_DURATION_SECONDS, RESERVATIONS_ACTIVE_GAUGE
from solution2.utils.logging import get_logger

__all__ = [
    "AdmissionDecision",
    "BatchAdmissionResult",
    "BatchTaskSpec",
    "SyncExecutionResult",
    "run_admission_gate",
    "run_batch_admission_gate",
    "run_sync_submission",
]

_SCRIPT_HASH_PLACEHOLDER: Final[str] = ""
logger = get_logger("solution2.services.billing")


@dataclass(frozen=True)
class BatchTaskSpec:
    x: int
    y: int
    model_class: ModelClass
    callback_url: str | None = None


@dataclass(frozen=True)
class BatchAdmissionResult:
    ok: bool
    reason: str
    task_ids: tuple[UUID, ...] = ()
    total_cost: int = 0


@dataclass(frozen=True)
class SyncExecutionResult:
    status: TaskStatus
    result: dict[str, int] | None
    error: str | None
    runtime_ms: int | None


def _coerce_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        with suppress(ValueError):
            return int(value)
    return default


def _normalize_payload(
    *,
    stream_payload: dict[str, object] | None,
    request_mode: str | RequestMode | None,
) -> tuple[dict[str, object], RequestMode, SubscriptionTier, str]:
    payload = stream_payload or {}
    payload_mode = request_mode or payload.get("mode", RequestMode.ASYNC.value)
    payload_tier = payload.get("tier", SubscriptionTier.FREE.value)
    payload_model_class = payload.get("model_class")
    if not isinstance(payload_model_class, str):
        raise ValueError("invalid model_class in submit payload")

    return (
        payload,
        RequestMode(str(payload_mode)),
        SubscriptionTier(str(payload_tier)),
        payload_model_class,
    )


def _resolve_queue_name(
    *,
    resolved_tier: SubscriptionTier,
    resolved_mode: RequestMode,
    model_class: str,
    queue_name: str | None,
) -> str:
    if queue_name is not None:
        return queue_name
    return resolve_queue(
        tier=resolved_tier,
        mode=resolved_mode,
        model_class=model_class,
    )


def _reservation_expires_at(*, reservation_ttl_seconds: int | None) -> datetime:
    ttl_seconds = reservation_ttl_seconds
    if ttl_seconds is None or ttl_seconds <= 0:
        ttl_seconds = 1
    return datetime.now(tz=UTC) + timedelta(seconds=ttl_seconds)


async def _execute_admission_transaction(
    *,
    db_pool: asyncpg.Pool,
    user_id: UUID,
    task_id: UUID,
    cost: int,
    idempotency_value: str,
    max_concurrent: int,
    stream_payload: dict[str, object] | None,
    request_mode: str | RequestMode | None,
    queue_name: str | None,
    reservation_ttl_seconds: int | None,
) -> AdmissionDecision:
    async with db_pool.acquire() as connection, connection.transaction():
        locked = await lock_user_for_admission(connection, user_id=user_id)
        if not locked:
            return AdmissionDecision(ok=False, reason="ERROR", existing_task_id=None)

        command_exists = await get_task_command_by_idempotency(
            connection,
            user_id=user_id,
            idempotency_key=idempotency_value,
        )
        if command_exists is not None:
            return AdmissionDecision(
                ok=False,
                reason="IDEMPOTENT",
                existing_task_id=str(command_exists.task_id),
            )

        active_count = await count_active_reservations(connection, user_id=user_id)
        if active_count >= max_concurrent:
            return AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None)

        remaining_credits = await reserve_user_credits(
            connection,
            user_id=user_id,
            amount=cost,
        )
        if remaining_credits is None:
            return AdmissionDecision(ok=False, reason="INSUFFICIENT", existing_task_id=None)

        payload, resolved_mode, resolved_tier, model_class = _normalize_payload(
            stream_payload=stream_payload,
            request_mode=request_mode,
        )
        selected_queue = _resolve_queue_name(
            resolved_tier=resolved_tier,
            resolved_mode=resolved_mode,
            model_class=model_class,
            queue_name=queue_name,
        )
        routing_key = compute_routing_key(
            mode=resolved_mode,
            tier=resolved_tier,
            model_class=model_class,
        )
        callback_value = payload.get("callback_url")
        callback_url = (
            callback_value if isinstance(callback_value, str) or callback_value is None else None
        )

        await create_task_command(
            connection,
            task_id=task_id,
            user_id=user_id,
            tier=resolved_tier,
            mode=resolved_mode,
            model_class=model_class,
            cost=cost,
            x=_coerce_int(payload.get("x", 0)),
            y=_coerce_int(payload.get("y", 0)),
            callback_url=callback_url,
            idempotency_key=idempotency_value,
        )

        await create_reservation(
            connection,
            task_id=task_id,
            user_id=user_id,
            amount=cost,
            expires_at=_reservation_expires_at(reservation_ttl_seconds=reservation_ttl_seconds),
        )

        await create_outbox_event(
            connection,
            aggregate_id=task_id,
            event_type="task.submitted",
            routing_key=str(routing_key),
            payload={
                "task_id": str(task_id),
                "user_id": str(user_id),
                "mode": resolved_mode.value,
                "tier": resolved_tier.value,
                "model_class": model_class,
                "cost": cost,
                "x": payload.get("x"),
                "y": payload.get("y"),
                "idempotency_key": idempotency_value,
                "callback_url": callback_url,
                "queue": selected_queue,
                "trace_id": payload.get("trace_id"),
            },
        )
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None)


def _batch_task_cost(*, base_cost: int, model_class: ModelClass) -> int:
    return task_cost_for_model(base_cost=base_cost, model_class=model_class)


async def _execute_batch_admission_transaction(
    *,
    db_pool: asyncpg.Pool,
    user_id: UUID,
    user_tier: SubscriptionTier,
    batch_id: UUID,
    tasks: tuple[BatchTaskSpec, ...],
    max_concurrent: int,
    base_task_cost: int,
    reservation_ttl_seconds: int | None,
    trace_id: str | None,
) -> BatchAdmissionResult:
    if not tasks:
        return BatchAdmissionResult(ok=False, reason="ERROR")

    async with db_pool.acquire() as connection, connection.transaction():
        locked = await lock_user_for_admission(connection, user_id=user_id)
        if not locked:
            return BatchAdmissionResult(ok=False, reason="ERROR")

        active_count = await count_active_reservations(connection, user_id=user_id)
        if active_count + len(tasks) > max_concurrent:
            return BatchAdmissionResult(ok=False, reason="CONCURRENCY")

        total_cost = sum(
            _batch_task_cost(base_cost=base_task_cost, model_class=task.model_class)
            for task in tasks
        )
        remaining_credits = await reserve_user_credits(
            connection,
            user_id=user_id,
            amount=total_cost,
        )
        if remaining_credits is None:
            return BatchAdmissionResult(ok=False, reason="INSUFFICIENT")

        created_task_ids: list[UUID] = []
        for index, task in enumerate(tasks):
            task_id = uuid7()
            mode = RequestMode.BATCH
            queue_name = resolve_queue(
                tier=user_tier,
                mode=mode,
                model_class=task.model_class,
            )
            routing_key = compute_routing_key(
                mode=mode,
                tier=user_tier,
                model_class=task.model_class,
            )
            cost = _batch_task_cost(base_cost=base_task_cost, model_class=task.model_class)

            await create_task_command(
                connection,
                task_id=task_id,
                user_id=user_id,
                tier=user_tier,
                mode=mode,
                model_class=task.model_class.value,
                cost=cost,
                x=task.x,
                y=task.y,
                callback_url=task.callback_url,
                idempotency_key=f"{batch_id}:{index}",
            )
            await create_reservation(
                connection,
                task_id=task_id,
                user_id=user_id,
                amount=cost,
                expires_at=_reservation_expires_at(reservation_ttl_seconds=reservation_ttl_seconds),
            )
            await create_outbox_event(
                connection,
                aggregate_id=task_id,
                event_type="task.submitted",
                routing_key=routing_key,
                payload={
                    "task_id": str(task_id),
                    "user_id": str(user_id),
                    "mode": mode.value,
                    "tier": user_tier.value,
                    "model_class": task.model_class.value,
                    "cost": cost,
                    "x": task.x,
                    "y": task.y,
                    "idempotency_key": f"{batch_id}:{index}",
                    "callback_url": task.callback_url,
                    "queue": queue_name,
                    "trace_id": trace_id,
                },
            )
            created_task_ids.append(task_id)

        return BatchAdmissionResult(
            ok=True,
            reason="OK",
            task_ids=tuple(created_task_ids),
            total_cost=total_cost,
        )


async def _execute_sync_admission_transaction(
    *,
    db_pool: asyncpg.Pool,
    user_id: UUID,
    user_tier: SubscriptionTier,
    task_id: UUID,
    x: int,
    y: int,
    model_class: ModelClass,
    cost: int,
    callback_url: str | None,
    idempotency_value: str,
    max_concurrent: int,
    reservation_ttl_seconds: int | None,
) -> AdmissionDecision:
    async with db_pool.acquire() as connection, connection.transaction():
        locked = await lock_user_for_admission(connection, user_id=user_id)
        if not locked:
            return AdmissionDecision(ok=False, reason="ERROR", existing_task_id=None)

        command_exists = await get_task_command_by_idempotency(
            connection,
            user_id=user_id,
            idempotency_key=idempotency_value,
        )
        if command_exists is not None:
            return AdmissionDecision(
                ok=False,
                reason="IDEMPOTENT",
                existing_task_id=str(command_exists.task_id),
            )

        active_count = await count_active_reservations(connection, user_id=user_id)
        if active_count >= max_concurrent:
            return AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None)

        remaining_credits = await reserve_user_credits(
            connection,
            user_id=user_id,
            amount=cost,
        )
        if remaining_credits is None:
            return AdmissionDecision(ok=False, reason="INSUFFICIENT", existing_task_id=None)

        await create_task_command(
            connection,
            task_id=task_id,
            user_id=user_id,
            tier=user_tier,
            mode=RequestMode.SYNC,
            model_class=model_class.value,
            cost=cost,
            x=x,
            y=y,
            callback_url=callback_url,
            idempotency_key=idempotency_value,
        )
        await create_reservation(
            connection,
            task_id=task_id,
            user_id=user_id,
            amount=cost,
            expires_at=_reservation_expires_at(reservation_ttl_seconds=reservation_ttl_seconds),
        )
        return AdmissionDecision(ok=True, reason="OK", existing_task_id=None)


async def _mark_sync_running(*, db_pool: asyncpg.Pool, task_id: UUID) -> bool:
    async with db_pool.acquire() as connection:
        return await update_task_command_running(connection, task_id=task_id)


async def _finalize_sync_success(
    *,
    db_pool: asyncpg.Pool,
    task_id: UUID,
    user_id: UUID,
    user_tier: SubscriptionTier,
    model_class: ModelClass,
    queue_name: str,
    result_payload: dict[str, int],
    runtime_ms: int,
) -> bool:
    async with db_pool.acquire() as connection, connection.transaction():
        status_updated = await update_task_command_completed(connection, task_id=task_id)
        if not status_updated:
            return False
        reservation = await get_credit_reservation(connection, task_id=task_id, for_update=True)
        if reservation is None:
            raise RuntimeError("sync reservation missing")
        captured = await capture_reservation(connection, task_id=task_id)
        if not captured:
            raise RuntimeError("sync reservation capture failed")

        await upsert_task_query_view(
            connection,
            task_id=task_id,
            user_id=user_id,
            tier=user_tier,
            mode=RequestMode.SYNC,
            model_class=model_class.value,
            status=TaskStatus.COMPLETED,
            result=cast(dict[str, object], result_payload),
            error=None,
            queue_name=queue_name,
            runtime_ms=runtime_ms,
        )
    return True


async def _finalize_sync_failure(
    *,
    db_pool: asyncpg.Pool,
    task_id: UUID,
    user_id: UUID,
    user_tier: SubscriptionTier,
    model_class: ModelClass,
    queue_name: str,
    status: TaskStatus,
    error_message: str,
) -> bool:
    async with db_pool.acquire() as connection, connection.transaction():
        if status == TaskStatus.TIMEOUT:
            status_updated = await update_task_command_timed_out(connection, task_id=task_id)
            refund_reason = "task_timeout_refund"
        else:
            status_updated = await update_task_command_failed(connection, task_id=task_id)
            refund_reason = "task_failed_refund"
        if not status_updated:
            return False

        reservation = await get_credit_reservation(connection, task_id=task_id, for_update=True)
        if reservation is None:
            raise RuntimeError("sync reservation missing")
        released = await release_reservation(connection, task_id=task_id)
        if not released:
            raise RuntimeError("sync reservation release failed")

        updated_balance = await add_user_credits(
            connection,
            user_id=reservation.user_id,
            delta=reservation.amount,
        )
        if updated_balance is None:
            raise RuntimeError("sync refund user missing")

        await insert_credit_transaction(
            connection,
            user_id=reservation.user_id,
            task_id=task_id,
            delta=reservation.amount,
            reason=refund_reason,
        )
        await upsert_task_query_view(
            connection,
            task_id=task_id,
            user_id=user_id,
            tier=user_tier,
            mode=RequestMode.SYNC,
            model_class=model_class.value,
            status=status,
            result=None,
            error=error_message,
            queue_name=queue_name,
            runtime_ms=None,
        )
    return True


async def run_admission_gate(
    *,
    admission_script_sha: str,
    user_id: UUID,
    task_id: UUID,
    cost: int,
    idempotency_value: str,
    max_concurrent: int,
    stream_payload: dict[str, object] | None = None,
    db_pool: asyncpg.Pool | None = None,
    request_mode: str | RequestMode | None = None,
    queue_name: str | None = None,
    reservation_ttl_seconds: int | None = None,
) -> tuple[AdmissionDecision, str]:
    """Execute submit-side admission checks using transactional PG state."""

    if db_pool is None:
        raise ValueError("db_pool is required for Solution 2 admission gate")

    start = time.perf_counter()
    try:
        decision = await _execute_admission_transaction(
            db_pool=db_pool,
            user_id=user_id,
            task_id=task_id,
            cost=cost,
            idempotency_value=idempotency_value,
            max_concurrent=max_concurrent,
            stream_payload=stream_payload,
            request_mode=request_mode,
            queue_name=queue_name,
            reservation_ttl_seconds=reservation_ttl_seconds,
        )
        if not decision.ok:
            return decision, admission_script_sha
        RESERVATIONS_ACTIVE_GAUGE.inc()
        return decision, _SCRIPT_HASH_PLACEHOLDER
    except (asyncpg.LockNotAvailableError, asyncpg.QueryCanceledError):
        return AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None), (
            admission_script_sha
        )
    except asyncpg.UniqueViolationError:
        try:
            async with db_pool.acquire() as recovery_conn:
                command_exists = await get_task_command_by_idempotency(
                    recovery_conn,
                    user_id=user_id,
                    idempotency_key=idempotency_value,
                )
            if command_exists is not None:
                return (
                    AdmissionDecision(
                        ok=False,
                        reason="IDEMPOTENT",
                        existing_task_id=str(command_exists.task_id),
                    ),
                    admission_script_sha,
                )
        except Exception as recovery_error:
            logger.warning("admission_unique_recovery_failed", error=str(recovery_error))
    except Exception:
        # Ensure caller sees a deterministic admission failure if admission state writes fail.
        try:
            async with db_pool.acquire() as recovery_conn:
                await recovery_conn.execute(
                    "UPDATE users SET credits = LEAST(credits, credits) WHERE user_id=$1",
                    user_id,
                )
        except Exception as recovery_error:
            logger.warning("admission_error_recovery_failed", error=str(recovery_error))
        # Keep this path explicit for observability parity with the legacy redis gate.
    duration = time.perf_counter() - start
    CREDIT_LUA_DURATION_SECONDS.labels(result="ERROR").observe(duration)
    return (
        AdmissionDecision(ok=False, reason="ERROR", existing_task_id=None),
        _SCRIPT_HASH_PLACEHOLDER,
    )


async def run_batch_admission_gate(
    *,
    admission_script_sha: str,
    user_id: UUID,
    user_tier: SubscriptionTier,
    batch_id: UUID,
    tasks: tuple[BatchTaskSpec, ...],
    max_concurrent: int,
    base_task_cost: int,
    db_pool: asyncpg.Pool | None = None,
    reservation_ttl_seconds: int | None = None,
    trace_id: str | None = None,
) -> tuple[BatchAdmissionResult, str]:
    if db_pool is None:
        raise ValueError("db_pool is required for Solution 2 batch admission")
    start = time.perf_counter()
    try:
        result = await _execute_batch_admission_transaction(
            db_pool=db_pool,
            user_id=user_id,
            user_tier=user_tier,
            batch_id=batch_id,
            tasks=tasks,
            max_concurrent=max_concurrent,
            base_task_cost=base_task_cost,
            reservation_ttl_seconds=reservation_ttl_seconds,
            trace_id=trace_id,
        )
        if result.ok:
            RESERVATIONS_ACTIVE_GAUGE.inc(float(len(result.task_ids)))
            return result, _SCRIPT_HASH_PLACEHOLDER
        return result, admission_script_sha
    except (asyncpg.LockNotAvailableError, asyncpg.QueryCanceledError):
        return BatchAdmissionResult(ok=False, reason="CONCURRENCY"), admission_script_sha
    except Exception:
        duration = time.perf_counter() - start
        CREDIT_LUA_DURATION_SECONDS.labels(result="ERROR").observe(duration)
        return BatchAdmissionResult(ok=False, reason="ERROR"), _SCRIPT_HASH_PLACEHOLDER


def _sync_error_outcome() -> tuple[AdmissionDecision, str, None]:
    return (
        AdmissionDecision(ok=False, reason="ERROR", existing_task_id=None),
        _SCRIPT_HASH_PLACEHOLDER,
        None,
    )


def _sync_terminal_outcome(
    *,
    status: TaskStatus,
    result: dict[str, int] | None,
    error: str | None,
    runtime_ms: int | None,
) -> tuple[AdmissionDecision, str, SyncExecutionResult]:
    return (
        AdmissionDecision(ok=True, reason="OK", existing_task_id=None),
        _SCRIPT_HASH_PLACEHOLDER,
        SyncExecutionResult(
            status=status,
            result=result,
            error=error,
            runtime_ms=runtime_ms,
        ),
    )


async def _execute_sync_runtime(
    *,
    db_pool: asyncpg.Pool,
    task_id: UUID,
    user_id: UUID,
    user_tier: SubscriptionTier,
    model_class: ModelClass,
    queue_name: str,
    execution_timeout_seconds: float,
    x: int,
    y: int,
) -> tuple[AdmissionDecision, str, SyncExecutionResult | None]:
    try:
        started_at = time.perf_counter()
        await asyncio.wait_for(
            asyncio.sleep(runtime_seconds_for_model(model_class)),
            timeout=max(0.1, execution_timeout_seconds),
        )
        runtime_ms = max(1, int((time.perf_counter() - started_at) * 1000))
        result_payload = {"z": x + y}
        finalized = await _finalize_sync_success(
            db_pool=db_pool,
            task_id=task_id,
            user_id=user_id,
            user_tier=user_tier,
            model_class=model_class,
            queue_name=queue_name,
            result_payload=result_payload,
            runtime_ms=runtime_ms,
        )
        if not finalized:
            return _sync_error_outcome()
        return _sync_terminal_outcome(
            status=TaskStatus.COMPLETED,
            result=result_payload,
            error=None,
            runtime_ms=runtime_ms,
        )
    except TimeoutError:
        finalized = await _finalize_sync_failure(
            db_pool=db_pool,
            task_id=task_id,
            user_id=user_id,
            user_tier=user_tier,
            model_class=model_class,
            queue_name=queue_name,
            status=TaskStatus.TIMEOUT,
            error_message="sync_execution_timeout",
        )
        if not finalized:
            return _sync_error_outcome()
        return _sync_terminal_outcome(
            status=TaskStatus.TIMEOUT,
            result=None,
            error="sync_execution_timeout",
            runtime_ms=None,
        )
    except Exception as exc:
        error_message = f"sync_execution_failed:{exc}"
        finalized = await _finalize_sync_failure(
            db_pool=db_pool,
            task_id=task_id,
            user_id=user_id,
            user_tier=user_tier,
            model_class=model_class,
            queue_name=queue_name,
            status=TaskStatus.FAILED,
            error_message=error_message,
        )
        if not finalized:
            return _sync_error_outcome()
        return _sync_terminal_outcome(
            status=TaskStatus.FAILED,
            result=None,
            error=error_message,
            runtime_ms=None,
        )


async def run_sync_submission(
    *,
    admission_script_sha: str,
    user_id: UUID,
    user_tier: SubscriptionTier,
    task_id: UUID,
    x: int,
    y: int,
    model_class: ModelClass,
    cost: int,
    callback_url: str | None,
    idempotency_value: str,
    max_concurrent: int,
    queue_name: str,
    execution_timeout_seconds: float,
    db_pool: asyncpg.Pool | None = None,
    reservation_ttl_seconds: int | None = None,
) -> tuple[AdmissionDecision, str, SyncExecutionResult | None]:
    if db_pool is None:
        raise ValueError("db_pool is required for Solution 2 sync submission")
    try:
        decision = await _execute_sync_admission_transaction(
            db_pool=db_pool,
            user_id=user_id,
            user_tier=user_tier,
            task_id=task_id,
            x=x,
            y=y,
            model_class=model_class,
            cost=cost,
            callback_url=callback_url,
            idempotency_value=idempotency_value,
            max_concurrent=max_concurrent,
            reservation_ttl_seconds=reservation_ttl_seconds,
        )
    except (asyncpg.LockNotAvailableError, asyncpg.QueryCanceledError):
        return (
            AdmissionDecision(ok=False, reason="CONCURRENCY", existing_task_id=None),
            admission_script_sha,
            None,
        )
    except Exception:
        return _sync_error_outcome()

    if not decision.ok:
        return decision, admission_script_sha, None

    marked_running = await _mark_sync_running(db_pool=db_pool, task_id=task_id)
    if not marked_running:
        return _sync_error_outcome()

    return await _execute_sync_runtime(
        db_pool=db_pool,
        task_id=task_id,
        user_id=user_id,
        user_tier=user_tier,
        model_class=model_class,
        queue_name=queue_name,
        execution_timeout_seconds=execution_timeout_seconds,
        x=x,
        y=y,
    )
