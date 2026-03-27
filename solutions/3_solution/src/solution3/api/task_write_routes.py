from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from uuid6 import uuid7

from solution3.api.error_responses import api_error_response
from solution3.api.paths import V1_TASK_CANCEL_PATH, V1_TASK_SUBMIT_PATH
from solution3.constants import (
    TASK_CANCELLABLE_STATUSES,
    BillingState,
    ModelClass,
    RequestMode,
    SubscriptionTier,
    TaskStatus,
    UserRole,
    task_cost_for_model,
)
from solution3.db.repository import cancel_task_command, get_task_command
from solution3.db.repository import submit_task_command as _submit_task_command
from solution3.models.domain import AuthUser, TaskCommand
from solution3.models.schemas import CancelTaskResponse, SubmitTaskRequest, SubmitTaskResponse
from solution3.observability.metrics import TASK_COMPLETIONS_TOTAL, TASK_SUBMISSIONS_TOTAL
from solution3.services.auth import (
    require_authenticated_user,
    require_scopes,
    runtime_state_from_request,
)
from solution3.services.billing import ReserveCreditsResult
from solution3.utils.logging import get_logger

AUTHENTICATED_USER = Depends(require_authenticated_user)
logger = get_logger("solution3.task_write")


@dataclass(frozen=True)
class SubmitCommandResult:
    created: bool
    command: TaskCommand


def _task_state_key(task_id: UUID) -> str:
    return f"task:{task_id}"


def _active_counter_key(user_id: UUID) -> str:
    return f"active:{user_id}"


def _max_concurrent(*, tier: SubscriptionTier, request: Request) -> int:
    settings = runtime_state_from_request(request).settings
    if tier == SubscriptionTier.FREE:
        return settings.max_concurrent_free
    if tier == SubscriptionTier.PRO:
        return settings.max_concurrent_pro
    return settings.max_concurrent_enterprise


def _expires_at(command: TaskCommand, *, ttl_seconds: int) -> datetime:
    return command.created_at + timedelta(seconds=ttl_seconds)


async def submit_task_command(
    pool: asyncpg.Pool,
    *,
    task_id: UUID,
    user_id: UUID,
    tier: SubscriptionTier,
    mode: RequestMode,
    model_class: ModelClass,
    x: int,
    y: int,
    cost: int,
    tb_pending_transfer_id: UUID,
    callback_url: str | None,
    idempotency_key: str | None,
    outbox_payload: dict[str, object],
) -> SubmitCommandResult:
    created, command = await _submit_task_command(
        pool,
        task_id=task_id,
        user_id=user_id,
        tier=tier,
        mode=mode,
        model_class=model_class,
        x=x,
        y=y,
        cost=cost,
        tb_pending_transfer_id=tb_pending_transfer_id,
        callback_url=callback_url,
        idempotency_key=idempotency_key,
        outbox_payload=outbox_payload,
    )
    return SubmitCommandResult(created=created, command=command)


async def _cache_task_state(*, request: Request, command: TaskCommand) -> None:
    runtime = runtime_state_from_request(request)
    redis_client = runtime.redis_client
    if redis_client is None:
        return
    await redis_client.hset(
        _task_state_key(command.task_id),
        mapping={
            "user_id": str(command.user_id),
            "status": command.status.value,
            "billing_state": command.billing_state.value,
            "model_class": command.model_class.value,
            "created_at_epoch": str(int(command.created_at.timestamp())),
        },
    )
    await redis_client.expire(
        _task_state_key(command.task_id), runtime.settings.task_result_ttl_seconds
    )


async def _release_pending_transfer(
    *, request: Request, pending_transfer_id: UUID, required: bool = True
) -> bool:
    runtime = runtime_state_from_request(request)
    billing_client = runtime.billing_client
    if billing_client is None:
        return not required
    released = await asyncio.to_thread(
        billing_client.void_pending_transfer,
        pending_transfer_id=pending_transfer_id,
    )
    return bool(released)


def _validate_idempotency(idempotency_key: str | None, *, generated_task_id: UUID) -> str:
    if idempotency_key is None:
        return str(generated_task_id)
    normalized = idempotency_key.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("Idempotency-Key must be between 1 and 128 characters")
    return normalized


def register_task_write_routes(router: APIRouter) -> None:
    @router.post(V1_TASK_SUBMIT_PATH, tags=["tasks"])
    async def submit_task(
        request: Request,
        payload: SubmitTaskRequest,
        current_user: AuthUser = AUTHENTICATED_USER,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        require_scopes(current_user=current_user, required_scopes=frozenset({"task:submit"}))
        runtime = runtime_state_from_request(request)
        if runtime.db_pool is None:
            TASK_SUBMISSIONS_TOTAL.labels(result="command_store_unavailable").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Command store unavailable",
            )
        if runtime.billing_client is None:
            TASK_SUBMISSIONS_TOTAL.labels(result="billing_unavailable").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Billing backend unavailable",
            )

        generated_task_id = uuid7()
        try:
            idempotency_value = _validate_idempotency(
                idempotency_key, generated_task_id=generated_task_id
            )
        except ValueError as exc:
            TASK_SUBMISSIONS_TOTAL.labels(result="bad_request").inc()
            return api_error_response(status_code=400, code="BAD_REQUEST", message=str(exc))

        redis_client = runtime.redis_client
        if redis_client is not None:
            active_count_raw = await redis_client.get(_active_counter_key(current_user.user_id))
            active_count = int(active_count_raw or "0")
            if active_count >= _max_concurrent(tier=current_user.tier, request=request):
                TASK_SUBMISSIONS_TOTAL.labels(result="concurrency_reject").inc()
                return api_error_response(
                    status_code=429,
                    code="TOO_MANY_REQUESTS",
                    message="Max concurrent tasks reached",
                )

        effective_cost = task_cost_for_model(
            base_cost=runtime.settings.task_cost, model_class=payload.model_class
        )
        pending_transfer_id = uuid7()
        reserve_result = await asyncio.to_thread(
            runtime.billing_client.reserve_credits,
            user_id=current_user.user_id,
            transfer_id=pending_transfer_id,
            amount=effective_cost,
        )
        if reserve_result == ReserveCreditsResult.INSUFFICIENT_CREDITS:
            TASK_SUBMISSIONS_TOTAL.labels(result="insufficient_credits").inc()
            return api_error_response(
                status_code=402,
                code="INSUFFICIENT_CREDITS",
                message="Insufficient credits",
            )
        if reserve_result != ReserveCreditsResult.ACCEPTED:
            TASK_SUBMISSIONS_TOTAL.labels(result="billing_unavailable").inc()
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Billing backend unavailable",
            )

        if redis_client is not None:
            active_count_raw = await redis_client.get(_active_counter_key(current_user.user_id))
            active_count = int(active_count_raw or "0")
            if active_count >= _max_concurrent(tier=current_user.tier, request=request):
                await _release_pending_transfer(
                    request=request, pending_transfer_id=pending_transfer_id
                )
                TASK_SUBMISSIONS_TOTAL.labels(result="concurrency_reject").inc()
                return api_error_response(
                    status_code=429,
                    code="TOO_MANY_REQUESTS",
                    message="Max concurrent tasks reached",
                )
        try:
            submit_result = await submit_task_command(
                runtime.db_pool,
                task_id=generated_task_id,
                user_id=current_user.user_id,
                tier=current_user.tier,
                mode=payload.mode,
                model_class=payload.model_class,
                x=payload.x,
                y=payload.y,
                cost=effective_cost,
                tb_pending_transfer_id=pending_transfer_id,
                callback_url=payload.callback_url,
                idempotency_key=idempotency_value,
                outbox_payload={
                    "task_id": str(generated_task_id),
                    "user_id": str(current_user.user_id),
                    "tier": current_user.tier.value,
                    "mode": payload.mode.value,
                    "model_class": payload.model_class.value,
                    "x": payload.x,
                    "y": payload.y,
                    "cost": effective_cost,
                    "tb_pending_transfer_id": str(pending_transfer_id),
                },
            )
        except Exception:
            await _release_pending_transfer(
                request=request, pending_transfer_id=pending_transfer_id
            )
            raise
        created = submit_result.created
        command = submit_result.command

        if not created:
            released = await _release_pending_transfer(
                request=request, pending_transfer_id=pending_transfer_id
            )
            if not released:
                TASK_SUBMISSIONS_TOTAL.labels(result="billing_unavailable").inc()
                return api_error_response(
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Billing backend unavailable",
                )
            same_payload = (
                command.x == payload.x
                and command.y == payload.y
                and command.mode == payload.mode
                and command.model_class == payload.model_class
                and command.callback_url == payload.callback_url
                and command.cost == effective_cost
            )
            if not same_payload:
                TASK_SUBMISSIONS_TOTAL.labels(result="idempotency_conflict").inc()
                return api_error_response(
                    status_code=409,
                    code="CONFLICT",
                    message="Idempotency key reused with different payload",
                )
        else:
            if redis_client is not None:
                await redis_client.incr(_active_counter_key(current_user.user_id))

        await _cache_task_state(request=request, command=command)
        TASK_SUBMISSIONS_TOTAL.labels(result="accepted" if created else "idempotent").inc()
        response = SubmitTaskResponse(
            task_id=command.task_id,
            status=command.status.value,
            billing_state=command.billing_state.value,
            queue=None,
            expires_at=_expires_at(command, ttl_seconds=runtime.settings.task_result_ttl_seconds),
        )
        return JSONResponse(
            status_code=201 if created else 200,
            content=response.model_dump(mode="json"),
        )

    @router.post(V1_TASK_CANCEL_PATH, tags=["tasks"])
    async def cancel_task(
        task_id: UUID,
        request: Request,
        current_user: AuthUser = AUTHENTICATED_USER,
    ) -> JSONResponse:
        require_scopes(current_user=current_user, required_scopes=frozenset({"task:cancel"}))
        runtime = runtime_state_from_request(request)
        if runtime.db_pool is None:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Command store unavailable",
            )

        command = await get_task_command(runtime.db_pool, task_id)
        if command is None:
            return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")
        if current_user.role != UserRole.ADMIN and command.user_id != current_user.user_id:
            return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")
        if (
            command.status.value not in TASK_CANCELLABLE_STATUSES
            or command.billing_state != BillingState.RESERVED
        ):
            return api_error_response(
                status_code=409,
                code="CONFLICT",
                message="Task can no longer be cancelled",
            )
        if runtime.billing_client is None:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Billing backend unavailable",
            )

        cancelled = await cancel_task_command(runtime.db_pool, task_id=task_id)
        if not cancelled:
            return api_error_response(
                status_code=409,
                code="CONFLICT",
                message="Task can no longer be cancelled",
            )

        try:
            released = await _release_pending_transfer(
                request=request,
                pending_transfer_id=command.tb_pending_transfer_id,
                required=False,
            )
        except Exception:
            logger.warning(
                "solution3_task_cancel_billing_void_failed",
                task_id=str(task_id),
                user_id=str(command.user_id),
            )
        else:
            if not released:
                logger.warning(
                    "solution3_task_cancel_billing_void_skipped",
                    task_id=str(task_id),
                    user_id=str(command.user_id),
                )

        redis_client = runtime.redis_client
        if redis_client is not None:
            await redis_client.decr(_active_counter_key(command.user_id))
            await redis_client.hset(
                _task_state_key(task_id),
                mapping={
                    "user_id": str(command.user_id),
                    "status": TaskStatus.CANCELLED.value,
                    "billing_state": BillingState.RELEASED.value,
                    "model_class": command.model_class.value,
                    "created_at_epoch": str(int(command.created_at.timestamp())),
                    "completed_at_epoch": str(int(datetime.now(tz=UTC).timestamp())),
                },
            )
            await redis_client.expire(
                _task_state_key(task_id), runtime.settings.task_result_ttl_seconds
            )

        response = CancelTaskResponse(
            task_id=task_id,
            status=TaskStatus.CANCELLED.value,
            billing_state=BillingState.RELEASED.value,
        )
        TASK_COMPLETIONS_TOTAL.labels(status=TaskStatus.CANCELLED.value).inc()
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
