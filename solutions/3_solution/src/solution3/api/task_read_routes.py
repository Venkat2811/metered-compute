from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from solution3.api.error_responses import api_error_response
from solution3.api.paths import V1_TASK_POLL_PATH
from solution3.constants import TaskStatus, UserRole
from solution3.db.repository import get_task_command, get_task_query_view
from solution3.models.domain import AuthUser, TaskCommand, TaskQueryView
from solution3.models.schemas import PollTaskResponse
from solution3.services.auth import (
    require_authenticated_user,
    require_scopes,
    runtime_state_from_request,
)

AUTHENTICATED_USER = Depends(require_authenticated_user)


def _task_state_key(task_id: UUID) -> str:
    return f"task:{task_id}"


def _authorized(*, current_user: AuthUser, user_id: str) -> bool:
    return current_user.role == UserRole.ADMIN or user_id == str(current_user.user_id)


def _cached_result(task_state: dict[str, str]) -> dict[str, object] | None:
    raw_result = task_state.get("result")
    if raw_result is None:
        return None
    try:
        decoded = json.loads(raw_result)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _command_poll_response(*, task: TaskCommand, ttl_seconds: int) -> PollTaskResponse:
    return PollTaskResponse(
        task_id=task.task_id,
        status=task.status.value,
        billing_state=task.billing_state.value,
        result=None,
        error=None,
        expires_at=task.updated_at + timedelta(seconds=ttl_seconds),
    )


def _query_poll_response(*, task: TaskQueryView, ttl_seconds: int) -> PollTaskResponse:
    return PollTaskResponse(
        task_id=task.task_id,
        status=task.status.value,
        billing_state=task.billing_state.value,
        result=task.result,
        error=task.error,
        expires_at=task.updated_at + timedelta(seconds=ttl_seconds),
    )


def register_task_read_routes(router: APIRouter) -> None:
    @router.get(V1_TASK_POLL_PATH, tags=["tasks"])
    async def poll_task(
        task_id: UUID,
        request: Request,
        current_user: AuthUser = AUTHENTICATED_USER,
    ) -> JSONResponse:
        require_scopes(current_user=current_user, required_scopes=frozenset({"task:poll"}))
        runtime = runtime_state_from_request(request)
        redis_client = runtime.redis_client
        if redis_client is not None:
            task_state = await redis_client.hgetall(_task_state_key(task_id))
            if task_state:
                task_user_id = task_state.get("user_id")
                if task_user_id is None or not _authorized(
                    current_user=current_user, user_id=task_user_id
                ):
                    return api_error_response(
                        status_code=404,
                        code="NOT_FOUND",
                        message="Task not found",
                    )
                response = PollTaskResponse(
                    task_id=task_id,
                    status=task_state.get("status", TaskStatus.PENDING.value),
                    billing_state=task_state.get("billing_state", "RESERVED"),
                    result=_cached_result(task_state),
                    error=task_state.get("error"),
                    expires_at=None,
                )
                return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

        if runtime.db_pool is None:
            return api_error_response(
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Command store unavailable",
            )

        query_view = await get_task_query_view(runtime.db_pool, task_id)
        if query_view is not None:
            if not _authorized(current_user=current_user, user_id=str(query_view.user_id)):
                return api_error_response(
                    status_code=404, code="NOT_FOUND", message="Task not found"
                )
            response = _query_poll_response(
                task=query_view,
                ttl_seconds=runtime.settings.task_result_ttl_seconds,
            )
            return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

        command = await get_task_command(runtime.db_pool, task_id)
        if command is None:
            return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")
        if not _authorized(current_user=current_user, user_id=str(command.user_id)):
            return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")
        response = _command_poll_response(
            task=command, ttl_seconds=runtime.settings.task_result_ttl_seconds
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
