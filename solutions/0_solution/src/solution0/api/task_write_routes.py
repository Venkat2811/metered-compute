"""Write-side task APIs (submit/cancel) with admission and compensation logic."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from uuid6 import uuid7

from solution0.api.contracts import TaskWriteRoutesApp
from solution0.api.paths import (
    COMPAT_TASK_CANCEL_PATH,
    COMPAT_TASK_SUBMIT_PATH,
    V1_TASK_CANCEL_PATH,
    V1_TASK_SUBMIT_PATH,
)
from solution0.models.domain import AuthUser
from solution0.models.schemas import CancelTaskResponse, SubmitTaskRequest, SubmitTaskResponse
from solution0.utils.retry import retry_async


def _error_response(
    app_module: TaskWriteRoutesApp,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    """Return the shared API error envelope as a typed JSON response."""
    return app_module._error_response(status_code=status_code, code=code, message=message)


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
        runtime = app_module._runtime_state(request)
        task_id = uuid7()
        idempotency_value = idempotency_header.strip() if idempotency_header else str(task_id)
        trace_id = cast(str, getattr(request.state, "trace_id", str(uuid.uuid4())))

        try:
            decision, runtime.admission_script_sha = await app_module.run_admission_gate(
                redis_client=runtime.redis_client,
                admission_script_sha=runtime.admission_script_sha,
                user_id=current_user.user_id,
                task_id=task_id,
                cost=runtime.settings.task_cost,
                idempotency_value=idempotency_value,
                idempotency_ttl_seconds=runtime.settings.idempotency_ttl_seconds,
                max_concurrent=runtime.settings.max_concurrent,
            )
        except Exception as exc:
            app_module.logger.exception("admission_gate_failed", error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="admission_failure").inc()
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )

        if decision.reason == "CACHE_MISS":
            try:
                hydrated = await app_module.hydrate_credits_from_db(
                    redis_client=runtime.redis_client,
                    db_pool=runtime.db_pool,
                    api_key=current_user.api_key,
                    user_id=current_user.user_id,
                )
            except Exception as exc:
                app_module.logger.exception("cache_miss_hydration_failed", error=str(exc))
                app_module.TASK_SUBMISSIONS_TOTAL.labels(
                    result="cache_miss_hydration_failure"
                ).inc()
                return _error_response(
                    app_module,
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Admission gate unavailable",
                )
            if not hydrated:
                app_module.TASK_SUBMISSIONS_TOTAL.labels(result="cache_miss_no_user").inc()
                return _error_response(
                    app_module,
                    status_code=401,
                    code="UNAUTHORIZED",
                    message="Missing or invalid bearer token",
                )

            try:
                decision, runtime.admission_script_sha = await app_module.run_admission_gate(
                    redis_client=runtime.redis_client,
                    admission_script_sha=runtime.admission_script_sha,
                    user_id=current_user.user_id,
                    task_id=task_id,
                    cost=runtime.settings.task_cost,
                    idempotency_value=idempotency_value,
                    idempotency_ttl_seconds=runtime.settings.idempotency_ttl_seconds,
                    max_concurrent=runtime.settings.max_concurrent,
                )
            except Exception as exc:
                app_module.logger.exception("admission_gate_retry_failed", error=str(exc))
                app_module.TASK_SUBMISSIONS_TOTAL.labels(result="admission_failure").inc()
                return _error_response(
                    app_module,
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Admission gate unavailable",
                )

        if not decision.ok:
            if decision.reason == "IDEMPOTENT" and decision.existing_task_id is not None:
                existing_id = UUID(decision.existing_task_id)
                try:
                    existing = await app_module.get_task(runtime.db_pool, existing_id)
                except Exception as exc:
                    app_module.logger.exception("idempotent_lookup_failed", error=str(exc))
                    app_module.TASK_SUBMISSIONS_TOTAL.labels(
                        result="idempotent_lookup_failure"
                    ).inc()
                    return _error_response(
                        app_module,
                        status_code=503,
                        code="SERVICE_DEGRADED",
                        message="Admission gate unavailable",
                    )
                if existing is not None and (existing.x != payload.x or existing.y != payload.y):
                    app_module.TASK_SUBMISSIONS_TOTAL.labels(result="idempotency_conflict").inc()
                    return _error_response(
                        app_module,
                        status_code=409,
                        code="CONFLICT",
                        message="Idempotency key reused with different payload",
                    )
                expires_at = (
                    app_module._task_expires_at(existing, runtime.settings.task_result_ttl_seconds)
                    if existing is not None
                    else datetime.now(tz=UTC)
                    + timedelta(seconds=runtime.settings.task_result_ttl_seconds)
                )
                app_module.TASK_SUBMISSIONS_TOTAL.labels(result="idempotent").inc()
                response = SubmitTaskResponse(
                    task_id=existing_id,
                    status=(
                        existing.status if existing is not None else app_module.DEFAULT_TASK_STATUS
                    ),
                    expires_at=expires_at,
                )
                return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

            if decision.reason == "CONCURRENCY":
                app_module.TASK_SUBMISSIONS_TOTAL.labels(result="concurrency_reject").inc()
                return _error_response(
                    app_module,
                    status_code=429,
                    code="TOO_MANY_REQUESTS",
                    message="Max concurrent tasks reached",
                )

            if decision.reason == "INSUFFICIENT":
                app_module.TASK_SUBMISSIONS_TOTAL.labels(result="insufficient_credits").inc()
                return _error_response(
                    app_module,
                    status_code=402,
                    code="INSUFFICIENT_CREDITS",
                    message="Not enough credits",
                )

            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="admission_failure").inc()
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )

        pending_key = app_module.pending_marker_key(task_id)
        try:
            # Recovery marker for the dual-write window:
            # credits/concurrency are already reserved in Redis admission,
            # but PG task row may not exist yet.
            pending_mapping = {
                "task_id": str(task_id),
                "user_id": str(current_user.user_id),
                "cost": str(runtime.settings.task_cost),
                "idempotency_value": idempotency_value,
                "created_at_epoch": str(int(time.time())),
            }
            redis_mapping = cast(
                Mapping[str | bytes, bytes | float | int | str],
                pending_mapping,
            )
            async with runtime.redis_client.pipeline(transaction=False) as pipeline:
                pipeline.hset(pending_key, mapping=redis_mapping)
                pipeline.expire(pending_key, runtime.settings.pending_marker_ttl_seconds)
                await pipeline.execute()
        except Exception as exc:
            app_module.logger.exception("pending_marker_write_failed", error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="pending_marker_failure").inc()
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Admission gate unavailable",
            )

        try:
            async with (
                _acquire_db_connection(runtime.db_pool) as connection,
                connection.transaction(),
            ):
                await app_module.create_task_record(
                    connection,
                    task_id=task_id,
                    api_key=current_user.api_key,
                    user_id=current_user.user_id,
                    x=payload.x,
                    y=payload.y,
                    cost=runtime.settings.task_cost,
                    idempotency_key=idempotency_header,
                )
                await app_module.insert_credit_transaction(
                    connection,
                    user_id=current_user.user_id,
                    task_id=task_id,
                    delta=-runtime.settings.task_cost,
                    reason="task_deduct",
                )
            app_module.CREDIT_DEDUCTIONS_TOTAL.labels(reason="task_deduct").inc()
        except Exception as exc:
            try:
                # Compensation path: return reserved credits + active slot,
                # and clear idempotency/pending markers.
                runtime.decrement_script_sha = await app_module.refund_and_decrement_active(
                    redis_client=runtime.redis_client,
                    decrement_script_sha=runtime.decrement_script_sha,
                    user_id=current_user.user_id,
                    amount=runtime.settings.task_cost,
                )
                await runtime.redis_client.delete(
                    app_module.idempotency_key(current_user.user_id, idempotency_value)
                )
                await runtime.redis_client.delete(pending_key)
            except Exception as compensation_exc:
                app_module.logger.exception(
                    "task_persist_compensation_failed",
                    task_id=str(task_id),
                    error=str(compensation_exc),
                )

            app_module.logger.exception("task_persist_failed", task_id=str(task_id), error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="persist_failure").inc()
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Unable to persist task",
            )

        try:
            await asyncio.to_thread(
                app_module.celery_app.send_task,
                "solution0.run_task",
                args=[
                    str(task_id),
                    payload.x,
                    payload.y,
                    runtime.settings.task_cost,
                    str(current_user.user_id),
                    current_user.api_key,
                    trace_id,
                ],
                task_id=str(task_id),
            )
        except Exception as exc:
            try:
                runtime.decrement_script_sha = await app_module.refund_and_decrement_active(
                    redis_client=runtime.redis_client,
                    decrement_script_sha=runtime.decrement_script_sha,
                    user_id=current_user.user_id,
                    amount=runtime.settings.task_cost,
                )
                await runtime.redis_client.delete(
                    app_module.idempotency_key(current_user.user_id, idempotency_value)
                )
                async with _acquire_db_connection(runtime.db_pool) as connection:
                    async with connection.transaction():
                        failure_updated = await app_module.update_task_failed(
                            connection, task_id=task_id, error=f"publish_failed: {exc}"
                        )
                    if failure_updated:
                        await app_module.insert_credit_transaction(
                            connection,
                            user_id=current_user.user_id,
                            task_id=task_id,
                            delta=runtime.settings.task_cost,
                            reason="publish_refund",
                        )
                app_module.CREDIT_DEDUCTIONS_TOTAL.labels(reason="publish_refund").inc()
            except Exception as compensation_exc:
                app_module.logger.exception(
                    "task_publish_compensation_failed",
                    task_id=str(task_id),
                    error=str(compensation_exc),
                )
            try:
                await runtime.redis_client.delete(pending_key)
            except Exception:
                app_module.logger.exception("pending_marker_cleanup_failed", task_id=str(task_id))

            app_module.logger.exception("task_publish_failed", task_id=str(task_id), error=str(exc))
            app_module.TASK_SUBMISSIONS_TOTAL.labels(result="publish_failure").inc()
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Unable to enqueue task",
            )

        try:
            await runtime.redis_client.delete(pending_key)
        except Exception:
            app_module.logger.exception("pending_marker_cleanup_failed", task_id=str(task_id))

        response = SubmitTaskResponse(
            task_id=task_id,
            status=app_module.DEFAULT_TASK_STATUS,
            expires_at=datetime.now(tz=UTC)
            + timedelta(seconds=runtime.settings.task_result_ttl_seconds),
        )
        app_module.TASK_SUBMISSIONS_TOTAL.labels(result="accepted").inc()
        app_module.logger.info(
            "task_submitted", task_id=str(task_id), user_id=str(current_user.user_id)
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
        runtime = app_module._runtime_state(request)
        try:
            task = await app_module.get_task(runtime.db_pool, task_id)
        except Exception:
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if task is None:
            return _error_response(
                app_module, status_code=404, code="NOT_FOUND", message="Task not found"
            )
        if current_user.role != app_module.ADMIN_ROLE and task.user_id != current_user.user_id:
            return _error_response(
                app_module, status_code=404, code="NOT_FOUND", message="Task not found"
            )
        if task.status not in app_module.TASK_CANCELLABLE_STATUSES:
            return _error_response(
                app_module, status_code=409, code="CONFLICT", message="Task is not cancellable"
            )

        try:
            retry_attempts = int(getattr(runtime.settings, "redis_retry_attempts", 3))
            retry_base_delay = float(
                getattr(runtime.settings, "redis_retry_base_delay_seconds", 0.05)
            )
            retry_max_delay = float(getattr(runtime.settings, "redis_retry_max_delay_seconds", 0.5))

            async with (
                _acquire_db_connection(runtime.db_pool) as connection,
                connection.transaction(),
            ):
                # Guarded transition prevents cancel from overwriting terminal worker states.
                cancellation_applied = await app_module.update_task_cancelled(
                    connection, task_id=task_id
                )
                if not cancellation_applied:
                    raise app_module._TaskCancellationConflict
                await app_module.insert_credit_transaction(
                    connection,
                    user_id=task.user_id,
                    task_id=task_id,
                    delta=task.cost,
                    reason="cancel_refund",
                )

            async def _refund_cancelled_task() -> str:
                return await app_module.refund_and_decrement_active(
                    redis_client=runtime.redis_client,
                    decrement_script_sha=runtime.decrement_script_sha,
                    user_id=task.user_id,
                    amount=task.cost,
                )

            runtime.decrement_script_sha = await retry_async(
                _refund_cancelled_task,
                attempts=retry_attempts,
                base_delay_seconds=retry_base_delay,
                max_delay_seconds=retry_max_delay,
            )
            try:
                await asyncio.to_thread(
                    app_module.celery_app.control.revoke,
                    str(task_id),
                    terminate=True,
                    signal="SIGTERM",
                )
            except Exception as revoke_error:
                app_module.logger.warning(
                    "cancel_revoke_failed",
                    task_id=str(task_id),
                    error=str(revoke_error),
                )
        except app_module._TaskCancellationConflict:
            return _error_response(
                app_module, status_code=409, code="CONFLICT", message="Task is not cancellable"
            )
        except Exception:
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        app_module.CREDIT_DEDUCTIONS_TOTAL.labels(reason="cancel_refund").inc()

        response = CancelTaskResponse(
            task_id=task_id,
            status=app_module.TaskStatus.CANCELLED,
            credits_refunded=task.cost,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
