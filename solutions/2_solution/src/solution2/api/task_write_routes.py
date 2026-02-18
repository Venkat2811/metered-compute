"""Write-side task APIs (submit/cancel) with admission and compensation logic."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from uuid6 import uuid7

from solution2.api.contracts import TaskWriteRoutesApp
from solution2.api.error_responses import api_error_response
from solution2.api.paths import (
    COMPAT_TASK_BATCH_PATH,
    COMPAT_TASK_CANCEL_PATH,
    COMPAT_TASK_SUBMIT_PATH,
    V1_TASK_BATCH_PATH,
    V1_TASK_CANCEL_PATH,
    V1_TASK_SUBMIT_PATH,
)
from solution2.constants import (
    ModelClass,
    OAuthScope,
    RequestMode,
    SubscriptionTier,
    compute_routing_key,
    max_concurrent_for_tier,
    resolve_queue,
    runtime_seconds_for_model,
    task_cost_for_model,
)
from solution2.core.runtime import RuntimeState
from solution2.models.domain import AdmissionDecision, AuthUser
from solution2.models.schemas import (
    BatchSubmitRequest,
    BatchSubmitResponse,
    CancelTaskResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
)
from solution2.observability.metrics import RESERVATIONS_ACTIVE_GAUGE, RESERVATIONS_RELEASED_TOTAL
from solution2.observability.tracing import inject_current_trace_context
from solution2.services.billing import BatchTaskSpec, SyncExecutionResult
from solution2.services.webhooks import (
    build_terminal_webhook_event,
    enqueue_terminal_webhook_event,
)
from solution2.utils.retry import retry_async


@dataclass(frozen=True)
class _CancelResult:
    user_id: UUID
    refunded_credits: int
    queue_name: str | None


def _validated_idempotency_value(
    app_module: TaskWriteRoutesApp,
    *,
    idempotency_header: str | None,
    generated_task_id: UUID,
) -> tuple[str | None, JSONResponse | None]:
    if idempotency_header is None:
        return str(generated_task_id), None

    idempotency_value = idempotency_header.strip()
    if not idempotency_value:
        return None, api_error_response(
            status_code=400,
            code="BAD_REQUEST",
            message="Idempotency-Key must be between 1 and 128 characters",
        )
    if len(idempotency_value) > 128:
        return None, api_error_response(
            status_code=400,
            code="BAD_REQUEST",
            message="Idempotency-Key must be between 1 and 128 characters",
        )
    return idempotency_value, None


def _redis_retry_settings(runtime: RuntimeState) -> tuple[int, float, float]:
    attempts = int(getattr(runtime.settings, "redis_retry_attempts", 3))
    base_delay_seconds = float(getattr(runtime.settings, "redis_retry_base_delay_seconds", 0.05))
    max_delay_seconds = float(getattr(runtime.settings, "redis_retry_max_delay_seconds", 0.5))
    return attempts, base_delay_seconds, max_delay_seconds


def _command_expires_at(command: object, ttl_seconds: int) -> datetime:
    created_at = cast(datetime, getattr(command, "created_at", None))
    return created_at + timedelta(seconds=ttl_seconds)


def _is_sync_inline_candidate(*, current_user: AuthUser, payload: SubmitTaskRequest) -> bool:
    return (
        payload.mode == RequestMode.SYNC
        and current_user.tier == SubscriptionTier.ENTERPRISE
        and payload.model_class == ModelClass.SMALL
    )


async def _write_task_state(
    *,
    app_module: TaskWriteRoutesApp,
    runtime: RuntimeState,
    task_id: UUID,
    state_payload: dict[str | bytes, bytes | float | int | str],
) -> None:
    task_key = app_module.task_state_key(task_id)
    await runtime.redis_client.hset(task_key, mapping=state_payload)
    await runtime.redis_client.expire(task_key, runtime.settings.redis_task_state_ttl_seconds)


async def _handle_submit_admission_rejection(
    app_module: TaskWriteRoutesApp,
    *,
    runtime: RuntimeState,
    current_user: AuthUser,
    payload: SubmitTaskRequest,
    decision: AdmissionDecision,
    idempotency_value: str,
    effective_task_cost: int,
    estimated_seconds: int,
) -> JSONResponse | None:
    if decision.ok:
        return None

    if decision.reason == "IDEMPOTENT" and decision.existing_task_id is not None:
        existing_id = UUID(decision.existing_task_id)
        try:
            existing = await app_module.get_task_command(runtime.db_pool, existing_id)
        except Exception as exc:
            app_module.logger.exception("idempotent_lookup_failed", error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="idempotent_lookup_failure").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )
        if existing is None:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Task replay state unavailable",
            )
        if (
            existing.x != payload.x
            or existing.y != payload.y
            or existing.cost != effective_task_cost
            or existing.mode != payload.mode
            or existing.model_class != payload.model_class
            or existing.callback_url != payload.callback_url
        ):
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="idempotency_conflict").inc()
            return api_error_response(
                status_code=409,
                code="CONFLICT",
                message="Idempotency key reused with different payload",
            )

        existing_queue = resolve_queue(
            tier=existing.tier,
            mode=existing.mode,
            model_class=existing.model_class,
        )
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="idempotent").inc()
        app_module.logger.info(
            "business_event_task_idempotent_replay",
            task_id=str(existing_id),
            user_id=str(current_user.user_id),
            idempotency_key=idempotency_value,
        )
        response = SubmitTaskResponse(
            task_id=existing_id,
            status=existing.status,
            estimated_seconds=estimated_seconds,
            queue=existing_queue,
            expires_at=_command_expires_at(
                existing,
                runtime.settings.task_result_ttl_seconds,
            ),
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    if decision.reason == "CONCURRENCY":
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="concurrency_reject").inc()
        app_module.logger.info(
            "business_event_task_rejected",
            reason="CONCURRENCY",
            user_id=str(current_user.user_id),
            idempotency_key=idempotency_value,
        )
        return api_error_response(
            status_code=429,
            code="TOO_MANY_REQUESTS",
            message="Max concurrent tasks reached",
        )

    if decision.reason == "INSUFFICIENT":
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="insufficient_credits").inc()
        app_module.logger.info(
            "business_event_task_rejected",
            reason="INSUFFICIENT",
            user_id=str(current_user.user_id),
            idempotency_key=idempotency_value,
        )
        return api_error_response(
            status_code=402,
            code="INSUFFICIENT_CREDITS",
            message="Not enough credits",
        )

    app_module.TASK_SUBMISSIONS_TOTAL.labels(result="admission_failure").inc()
    return api_error_response(
        status_code=503,
        code="SERVICE_DEGRADED",
        message="Admission gate unavailable",
    )


def _batch_rejection_response(
    app_module: TaskWriteRoutesApp,
    *,
    reason: str,
) -> JSONResponse | None:
    if reason == "CONCURRENCY":
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="batch_concurrency_reject").inc()
        return api_error_response(
            status_code=429,
            code="TOO_MANY_REQUESTS",
            message="Max concurrent tasks reached",
        )
    if reason == "INSUFFICIENT":
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="batch_insufficient_credits").inc()
        return api_error_response(
            status_code=402,
            code="INSUFFICIENT_CREDITS",
            message="Not enough credits",
        )
    if reason != "OK":
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="batch_admission_failure").inc()
        return api_error_response(
            status_code=503,
            code="SERVICE_DEGRADED",
            message="Admission gate unavailable",
        )
    return None


async def _cache_batch_pending_states(
    *,
    app_module: TaskWriteRoutesApp,
    runtime: RuntimeState,
    current_user: AuthUser,
    task_specs: tuple[BatchTaskSpec, ...],
    task_ids: tuple[UUID, ...],
) -> None:
    created_at_epoch = str(int(time.time()))
    for index, task_id in enumerate(task_ids):
        task_spec = task_specs[index]
        queue_name = resolve_queue(
            tier=current_user.tier,
            mode=RequestMode.BATCH,
            model_class=task_spec.model_class,
        )
        effective_cost = task_cost_for_model(
            base_cost=runtime.settings.task_cost,
            model_class=task_spec.model_class,
        )
        await _write_task_state(
            app_module=app_module,
            runtime=runtime,
            task_id=task_id,
            state_payload={
                "status": app_module.DEFAULT_TASK_STATUS,
                "task_id": str(task_id),
                "user_id": str(current_user.user_id),
                "x": str(task_spec.x),
                "y": str(task_spec.y),
                "cost": str(effective_cost),
                "model_class": task_spec.model_class.value,
                "mode": RequestMode.BATCH.value,
                "queue": queue_name,
                "created_at_epoch": created_at_epoch,
            },
        )


async def _apply_cancel_transaction(
    app_module: TaskWriteRoutesApp,
    *,
    runtime: RuntimeState,
    task_id: UUID,
    default_queue_name: str,
) -> _CancelResult:
    async with runtime.db_pool.acquire() as connection, connection.transaction():
        command = await app_module.get_task_command(connection, task_id)
        if command is None:
            raise app_module._TaskCancellationConflict

        try:
            queue_name = resolve_queue(
                tier=command.tier,
                mode=command.mode,
                model_class=command.model_class,
            )
        except ValueError:
            queue_name = default_queue_name

        if command.status == app_module.TaskStatus.CANCELLED:
            return _CancelResult(
                user_id=command.user_id,
                refunded_credits=0,
                queue_name=queue_name,
            )
        if str(command.status) not in app_module.TASK_CANCELLABLE_STATUSES:
            raise app_module._TaskCancellationConflict

        reservation = await app_module.get_credit_reservation(
            connection,
            task_id=task_id,
            for_update=True,
        )
        if reservation is None:
            raise app_module._TaskCancellationConflict

        cancellation_applied = await app_module.update_task_command_cancelled(
            connection,
            task_id=task_id,
        )
        if not cancellation_applied:
            refreshed = await app_module.get_task_command(connection, task_id)
            if refreshed is not None and refreshed.status == app_module.TaskStatus.CANCELLED:
                return _CancelResult(
                    user_id=refreshed.user_id,
                    refunded_credits=0,
                    queue_name=queue_name,
                )
            raise app_module._TaskCancellationConflict

        released = await app_module.release_reservation(connection, task_id=task_id)
        if not released:
            raise app_module._TaskCancellationConflict

        refunded_credits = reservation.amount
        updated_balance = await app_module.add_user_credits(
            connection,
            user_id=reservation.user_id,
            delta=refunded_credits,
        )
        if updated_balance is None:
            raise RuntimeError("cancel_refund_target_missing")

        await app_module.insert_credit_transaction(
            connection,
            user_id=reservation.user_id,
            task_id=task_id,
            delta=refunded_credits,
            reason="cancel_refund",
        )

        routing_base = compute_routing_key(
            mode=command.mode,
            tier=command.tier,
            model_class=command.model_class,
        )
        routing_key = f"{routing_base}.cancelled"
        await app_module.create_outbox_event(
            connection,
            aggregate_id=task_id,
            event_type="task.cancelled",
            routing_key=routing_key,
            payload={
                "task_id": str(task_id),
                "user_id": str(command.user_id),
                "mode": command.mode.value,
                "tier": command.tier.value,
                "model_class": command.model_class.value,
                "queue": queue_name or "",
                "cost": command.cost,
                "status": app_module.TaskStatus.CANCELLED.value,
                "credits_refunded": refunded_credits,
            },
        )

        return _CancelResult(
            user_id=reservation.user_id,
            refunded_credits=refunded_credits,
            queue_name=queue_name,
        )


async def _sync_cancel_state_to_redis(
    *,
    app_module: TaskWriteRoutesApp,
    runtime: RuntimeState,
    task_id: UUID,
    task_user_id: UUID,
    queue_name: str | None,
) -> None:
    retry_attempts, retry_base_delay, retry_max_delay = _redis_retry_settings(runtime)

    async def _write_cancelled_task_state() -> None:
        await runtime.redis_client.hset(
            app_module.task_state_key(task_id),
            mapping={
                "status": app_module.TaskStatus.CANCELLED.value,
                "user_id": str(task_user_id),
                "queue": queue_name or "",
                "error": "",
                "completed_at_epoch": str(int(time.time())),
            },
        )

    await retry_async(
        _write_cancelled_task_state,
        attempts=retry_attempts,
        base_delay_seconds=retry_base_delay,
        max_delay_seconds=retry_max_delay,
    )

    async def _expire_cancelled_task_state() -> None:
        await runtime.redis_client.expire(
            app_module.task_state_key(task_id),
            runtime.settings.redis_task_state_ttl_seconds,
        )

    await retry_async(
        _expire_cancelled_task_state,
        attempts=retry_attempts,
        base_delay_seconds=retry_base_delay,
        max_delay_seconds=retry_max_delay,
    )


async def _enqueue_cancel_webhook(
    *,
    app_module: TaskWriteRoutesApp,
    runtime: RuntimeState,
    task_id: UUID,
    task_user_id: UUID,
) -> None:
    if not bool(getattr(runtime.settings, "webhook_enabled", True)):
        return
    try:
        event = build_terminal_webhook_event(
            user_id=task_user_id,
            task_id=task_id,
            status=app_module.TaskStatus.CANCELLED.value,
            result=None,
            error=None,
        )
        await enqueue_terminal_webhook_event(
            redis_client=runtime.redis_client,
            queue_key=str(getattr(runtime.settings, "webhook_queue_key", "webhook:queue")),
            event=event,
            max_queue_length=int(getattr(runtime.settings, "webhook_queue_maxlen", 100000)),
        )
    except Exception as exc:
        app_module.logger.warning(
            "webhook_event_enqueue_failed",
            task_id=str(task_id),
            event_status=app_module.TaskStatus.CANCELLED.value,
            error=str(exc),
        )


def register_task_write_routes(app: FastAPI, app_module: TaskWriteRoutesApp) -> None:
    """Register task mutation routes."""

    @app.post(COMPAT_TASK_SUBMIT_PATH, response_model=SubmitTaskResponse, tags=["compat"])
    @app.post(V1_TASK_SUBMIT_PATH, response_model=SubmitTaskResponse)
    async def submit_task(
        payload: SubmitTaskRequest,
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        """Admit a task, persist it, and enqueue worker execution."""
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.TASK_SUBMIT.value}),
        )
        runtime = app_module._runtime_state(request)
        task_id = uuid7()
        idempotency_value, idempotency_error = _validated_idempotency_value(
            app_module,
            idempotency_header=idempotency_header,
            generated_task_id=task_id,
        )
        if idempotency_error is not None:
            return idempotency_error
        if idempotency_value is None:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )

        trace_id = cast(str, getattr(request.state, "trace_id", str(uuid.uuid4())))
        effective_task_cost = task_cost_for_model(
            base_cost=runtime.settings.task_cost,
            model_class=payload.model_class,
        )
        estimated_seconds = int(runtime_seconds_for_model(payload.model_class))
        effective_max_concurrent = max_concurrent_for_tier(
            base_max_concurrent=runtime.settings.max_concurrent,
            tier=current_user.tier,
        )
        admission_payload: dict[str, object] = {
            "task_id": str(task_id),
            "user_id": str(current_user.user_id),
            "x": payload.x,
            "y": payload.y,
            "model_class": payload.model_class.value,
            "tier": current_user.tier.value,
            "cost": effective_task_cost,
            "callback_url": payload.callback_url,
            "api_key": current_user.api_key,
            "trace_id": trace_id,
        }
        trace_context = inject_current_trace_context()
        if trace_context:
            admission_payload["trace_context"] = trace_context

        try:
            queue_name = resolve_queue(
                tier=current_user.tier,
                mode=payload.mode,
                model_class=payload.model_class,
            )
        except ValueError:
            return api_error_response(
                status_code=400,
                code="BAD_REQUEST",
                message="Invalid request mode for user tier/model",
            )

        try:
            sync_result: SyncExecutionResult | None = None
            if _is_sync_inline_candidate(current_user=current_user, payload=payload):
                (
                    decision,
                    runtime.admission_script_sha,
                    sync_result,
                ) = await app_module.run_sync_submission(
                    admission_script_sha=runtime.admission_script_sha,
                    user_id=current_user.user_id,
                    user_tier=current_user.tier,
                    task_id=task_id,
                    x=payload.x,
                    y=payload.y,
                    model_class=payload.model_class,
                    cost=effective_task_cost,
                    callback_url=payload.callback_url,
                    idempotency_value=idempotency_value,
                    max_concurrent=effective_max_concurrent,
                    queue_name=queue_name,
                    execution_timeout_seconds=runtime.settings.sync_execution_timeout_seconds,
                    db_pool=runtime.db_pool,
                    reservation_ttl_seconds=runtime.settings.reservation_ttl_seconds,
                )
            else:
                decision, runtime.admission_script_sha = await app_module.run_admission_gate(
                    admission_script_sha=runtime.admission_script_sha,
                    user_id=current_user.user_id,
                    task_id=task_id,
                    cost=effective_task_cost,
                    idempotency_value=idempotency_value,
                    max_concurrent=effective_max_concurrent,
                    stream_payload=admission_payload,
                    db_pool=runtime.db_pool,
                    request_mode=payload.mode.value,
                    queue_name=queue_name,
                    reservation_ttl_seconds=runtime.settings.reservation_ttl_seconds,
                )
        except Exception as exc:
            app_module.logger.exception("admission_gate_failed", error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="admission_failure").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )

        rejection = await _handle_submit_admission_rejection(
            app_module,
            runtime=runtime,
            current_user=current_user,
            payload=payload,
            decision=decision,
            idempotency_value=idempotency_value,
            effective_task_cost=effective_task_cost,
            estimated_seconds=estimated_seconds,
        )
        if rejection is not None:
            return rejection

        is_inline_sync = sync_result is not None
        inline_sync_result = sync_result
        try:
            now_epoch = str(int(time.time()))
            status_value = app_module.DEFAULT_TASK_STATUS
            if is_inline_sync:
                if inline_sync_result is None:
                    return api_error_response(
                        status_code=503,
                        code="SERVICE_DEGRADED",
                        message="Admission gate unavailable",
                    )
                status_value = inline_sync_result.status.value
            state_payload: dict[str | bytes, bytes | float | int | str] = {
                "status": status_value,
                "task_id": str(task_id),
                "user_id": str(current_user.user_id),
                "x": str(payload.x),
                "y": str(payload.y),
                "cost": str(effective_task_cost),
                "model_class": payload.model_class.value,
                "mode": payload.mode.value,
                "queue": queue_name,
                "created_at_epoch": now_epoch,
            }
            if is_inline_sync:
                state_payload["completed_at_epoch"] = now_epoch
                if inline_sync_result is None:
                    return api_error_response(
                        status_code=503,
                        code="SERVICE_DEGRADED",
                        message="Admission gate unavailable",
                    )
                state_payload["error"] = inline_sync_result.error or ""
                if inline_sync_result.result is not None:
                    state_payload["result"] = json.dumps(inline_sync_result.result)
                if inline_sync_result.runtime_ms is not None:
                    state_payload["runtime_ms"] = str(inline_sync_result.runtime_ms)
            await _write_task_state(
                app_module=app_module,
                runtime=runtime,
                task_id=task_id,
                state_payload=state_payload,
            )
        except Exception as exc:
            app_module.logger.exception(
                "task_state_cache_write_failed",
                task_id=str(task_id),
                error=str(exc),
            )
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="cache_failure").inc()
            app_module.logger.exception("task_persist_failed", task_id=str(task_id), error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="persist_failure").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Unable to persist task",
            )

        if is_inline_sync:
            if inline_sync_result is None:
                return api_error_response(
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Admission gate unavailable",
                )
            if inline_sync_result.status == app_module.TaskStatus.TIMEOUT:
                app_module.TASK_SUBMISSIONS_TOTAL.labels(result="sync_timeout").inc()
                app_module.logger.info(
                    "business_event_task_sync_timeout",
                    task_id=str(task_id),
                    user_id=str(current_user.user_id),
                    idempotency_key=idempotency_value,
                    cost=effective_task_cost,
                    model_class=payload.model_class.value,
                    tier=current_user.tier.value,
                )
                return api_error_response(
                    status_code=408,
                    code="REQUEST_TIMEOUT",
                    message="Synchronous execution timed out",
                )

            result_label = (
                "sync_completed"
                if inline_sync_result.status == app_module.TaskStatus.COMPLETED
                else "sync_failed"
            )
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result=result_label).inc()
            app_module.logger.info(
                "business_event_task_submitted",
                task_id=str(task_id),
                user_id=str(current_user.user_id),
                idempotency_key=idempotency_value,
                cost=effective_task_cost,
                model_class=payload.model_class.value,
                tier=current_user.tier.value,
                mode=payload.mode.value,
                status=inline_sync_result.status.value,
            )
            sync_response = SubmitTaskResponse(
                task_id=task_id,
                status=inline_sync_result.status.value,
                estimated_seconds=estimated_seconds,
                result=inline_sync_result.result,
                error=inline_sync_result.error,
                runtime_ms=inline_sync_result.runtime_ms,
                queue=queue_name,
                expires_at=datetime.now(tz=UTC)
                + timedelta(seconds=runtime.settings.task_result_ttl_seconds),
            )
            return JSONResponse(status_code=201, content=sync_response.model_dump(mode="json"))

        response = SubmitTaskResponse(
            task_id=task_id,
            status=app_module.DEFAULT_TASK_STATUS,
            estimated_seconds=estimated_seconds,
            queue=queue_name,
            expires_at=datetime.now(tz=UTC)
            + timedelta(seconds=runtime.settings.task_result_ttl_seconds),
        )
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="accepted").inc()
        app_module.logger.info(
            "business_event_task_submitted",
            task_id=str(task_id),
            user_id=str(current_user.user_id),
            idempotency_key=idempotency_value,
            cost=effective_task_cost,
            model_class=payload.model_class.value,
            tier=current_user.tier.value,
        )
        return JSONResponse(status_code=201, content=response.model_dump(mode="json"))

    @app.post(COMPAT_TASK_BATCH_PATH, response_model=BatchSubmitResponse, tags=["compat"])
    @app.post(V1_TASK_BATCH_PATH, response_model=BatchSubmitResponse)
    async def submit_batch(
        payload: BatchSubmitRequest,
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        """Admit a batch request and create command+reservation rows in one transaction."""
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.TASK_SUBMIT.value}),
        )
        runtime = app_module._runtime_state(request)
        batch_id = uuid7()
        trace_id = cast(str, getattr(request.state, "trace_id", str(uuid.uuid4())))
        effective_max_concurrent = max_concurrent_for_tier(
            base_max_concurrent=runtime.settings.max_concurrent,
            tier=current_user.tier,
        )
        task_specs = tuple(
            BatchTaskSpec(
                x=task.x,
                y=task.y,
                model_class=task.model_class,
            )
            for task in payload.tasks
        )

        try:
            decision, runtime.admission_script_sha = await app_module.run_batch_admission_gate(
                admission_script_sha=runtime.admission_script_sha,
                user_id=current_user.user_id,
                user_tier=current_user.tier,
                batch_id=batch_id,
                tasks=task_specs,
                max_concurrent=effective_max_concurrent,
                base_task_cost=runtime.settings.task_cost,
                db_pool=runtime.db_pool,
                reservation_ttl_seconds=runtime.settings.reservation_ttl_seconds,
                trace_id=trace_id,
            )
        except Exception as exc:
            app_module.logger.exception("batch_admission_failed", error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="batch_admission_failure").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )

        rejection = _batch_rejection_response(app_module, reason=decision.reason)
        if rejection is not None:
            return rejection

        try:
            await _cache_batch_pending_states(
                app_module=app_module,
                runtime=runtime,
                current_user=current_user,
                task_specs=task_specs,
                task_ids=decision.task_ids,
            )
        except Exception as exc:
            app_module.logger.exception(
                "batch_task_state_cache_write_failed",
                batch_id=str(batch_id),
                error=str(exc),
            )
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="batch_cache_failure").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Unable to persist task batch",
            )

        response = BatchSubmitResponse(
            batch_id=batch_id,
            task_ids=list(decision.task_ids),
            total_cost=decision.total_cost,
        )
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="batch_accepted").inc()
        app_module.logger.info(
            "business_event_batch_submitted",
            batch_id=str(batch_id),
            user_id=str(current_user.user_id),
            task_count=len(decision.task_ids),
            total_cost=decision.total_cost,
            tier=current_user.tier.value,
        )
        return JSONResponse(status_code=201, content=response.model_dump(mode="json"))

    @app.post(COMPAT_TASK_CANCEL_PATH, response_model=CancelTaskResponse, tags=["compat"])
    @app.post(V1_TASK_CANCEL_PATH, response_model=CancelTaskResponse)
    async def cancel_task(
        task_id: UUID,
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        """Cancel a pending/running task and refund reserved credits."""
        app_module._require_scopes(
            current_user=current_user,
            required_scopes=frozenset({OAuthScope.TASK_CANCEL.value}),
        )
        runtime = app_module._runtime_state(request)
        try:
            command = await app_module.get_task_command(runtime.db_pool, task_id)
        except Exception:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if command is None:
            return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")
        if current_user.role != app_module.ADMIN_ROLE and command.user_id != current_user.user_id:
            return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")
        status_value = str(command.status)
        if status_value == app_module.TaskStatus.CANCELLED.value:
            try:
                queue_name = resolve_queue(
                    tier=command.tier,
                    mode=command.mode,
                    model_class=command.model_class,
                )
            except ValueError:
                queue_name = None
            await _sync_cancel_state_to_redis(
                app_module=app_module,
                runtime=runtime,
                task_id=task_id,
                task_user_id=command.user_id,
                queue_name=queue_name,
            )
            response = CancelTaskResponse(
                task_id=task_id,
                status=app_module.TaskStatus.CANCELLED,
                credits_refunded=0,
            )
            return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

        if status_value not in app_module.TASK_CANCELLABLE_STATUSES:
            return api_error_response(
                status_code=409, code="CONFLICT", message="Task is not cancellable"
            )

        try:
            try:
                default_queue_name = resolve_queue(
                    tier=command.tier,
                    mode=command.mode,
                    model_class=command.model_class,
                )
            except ValueError:
                default_queue_name = "queue.batch"

            cancel_result = await _apply_cancel_transaction(
                app_module,
                runtime=runtime,
                task_id=task_id,
                default_queue_name=default_queue_name,
            )
            await _sync_cancel_state_to_redis(
                app_module=app_module,
                runtime=runtime,
                task_id=task_id,
                task_user_id=cancel_result.user_id,
                queue_name=cancel_result.queue_name,
            )
        except app_module._TaskCancellationConflict:
            return api_error_response(
                status_code=409, code="CONFLICT", message="Task is not cancellable"
            )
        except Exception:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if cancel_result.refunded_credits > 0:
            app_module.CREDIT_DEDUCTIONS_TOTAL.labels(reason="cancel_refund").inc()
            RESERVATIONS_RELEASED_TOTAL.inc()
            RESERVATIONS_ACTIVE_GAUGE.dec()
        app_module.logger.info(
            "business_event_task_cancelled",
            task_id=str(task_id),
            user_id=str(cancel_result.user_id),
            credits_refunded=cancel_result.refunded_credits,
        )
        await _enqueue_cancel_webhook(
            app_module=app_module,
            runtime=runtime,
            task_id=task_id,
            task_user_id=cancel_result.user_id,
        )

        response = CancelTaskResponse(
            task_id=task_id,
            status=app_module.TaskStatus.CANCELLED,
            credits_refunded=cancel_result.refunded_credits,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
