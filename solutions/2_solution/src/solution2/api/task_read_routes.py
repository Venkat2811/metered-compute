"""Read-only task APIs (poll/status) with cache-first lookup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from solution2.api.contracts import TaskReadRoutesApp
from solution2.api.error_responses import api_error_response
from solution2.api.paths import COMPAT_TASK_POLL_PATH, V1_TASK_POLL_PATH
from solution2.constants import ModelClass, OAuthScope, runtime_seconds_for_model
from solution2.core.runtime import RuntimeState
from solution2.models.domain import AuthUser, TaskCommand, TaskQueryView
from solution2.models.schemas import PollTaskResponse


def _service_degraded() -> JSONResponse:
    return api_error_response(
        status_code=503,
        code="SERVICE_DEGRADED",
        message="Service temporarily unavailable",
    )


def _task_not_found() -> JSONResponse:
    return api_error_response(status_code=404, code="NOT_FOUND", message="Task not found")


def _authorized_for_user(
    app_module: TaskReadRoutesApp,
    *,
    current_user: AuthUser,
    task_user_id: str,
) -> bool:
    return current_user.role == app_module.ADMIN_ROLE or task_user_id == str(current_user.user_id)


def _parse_result_payload(raw_value: str | None) -> dict[str, object] | None:
    if raw_value is None or raw_value == "":
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _task_state_expires_at(task_state: dict[str, str], ttl_seconds: int) -> datetime | None:
    completed_at_epoch_raw = task_state.get("completed_at_epoch")
    created_at_epoch_raw = task_state.get("created_at_epoch")
    anchor_raw = completed_at_epoch_raw or created_at_epoch_raw
    if not anchor_raw or not anchor_raw.isdigit():
        return None

    anchor = datetime.fromtimestamp(int(anchor_raw), tz=UTC)
    return anchor + timedelta(seconds=ttl_seconds)


def _estimated_seconds_for_status(
    *,
    status: str,
    model_class: ModelClass | None,
) -> int | None:
    if status not in {"PENDING", "RUNNING"}:
        return None
    if model_class is None:
        return None
    return int(runtime_seconds_for_model(model_class))


def _task_state_response(
    app_module: TaskReadRoutesApp,
    *,
    task_id: UUID,
    task_state: dict[str, str],
    task_result_ttl_seconds: int,
) -> PollTaskResponse:
    status = task_state.get("status", app_module.DEFAULT_TASK_STATUS)
    model_class_raw = task_state.get("model_class")
    model_class: ModelClass | None
    try:
        model_class = ModelClass(model_class_raw) if model_class_raw else None
    except ValueError:
        model_class = None

    return PollTaskResponse(
        task_id=task_id,
        status=status,
        result=_parse_result_payload(task_state.get("result")),
        error=task_state.get("error") or None,
        queue=task_state.get("queue") or None,
        queue_position=None,
        estimated_seconds=_estimated_seconds_for_status(status=status, model_class=model_class),
        expires_at=_task_state_expires_at(task_state, task_result_ttl_seconds),
    )


def _projection_response(
    app_module: TaskReadRoutesApp,
    *,
    task: TaskQueryView,
    ttl_seconds: int,
) -> PollTaskResponse:
    status = task.status.value
    expires_at = task.updated_at + timedelta(seconds=ttl_seconds)
    if status in app_module.TASK_TERMINAL_STATUSES and datetime.now(tz=UTC) > expires_at:
        status = app_module.TaskStatus.EXPIRED.value

    return PollTaskResponse(
        task_id=task.task_id,
        status=status,
        result=task.result,
        error=task.error,
        queue=task.queue_name,
        queue_position=None,
        estimated_seconds=_estimated_seconds_for_status(
            status=status,
            model_class=task.model_class,
        ),
        expires_at=expires_at,
    )


def _command_response(*, task: TaskCommand, ttl_seconds: int) -> PollTaskResponse:
    status = task.status.value
    expires_at = task.updated_at + timedelta(seconds=ttl_seconds)
    if (
        status in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "EXPIRED"}
        and datetime.now(tz=UTC) > expires_at
    ):
        status = "EXPIRED"

    return PollTaskResponse(
        task_id=task.task_id,
        status=status,
        result=None,
        error=None,
        queue=None,
        queue_position=None,
        estimated_seconds=_estimated_seconds_for_status(
            status=status,
            model_class=task.model_class,
        ),
        expires_at=expires_at,
    )


async def _poll_from_task_state(
    app_module: TaskReadRoutesApp,
    *,
    runtime: RuntimeState,
    task_id: UUID,
    current_user: AuthUser,
) -> JSONResponse | None:
    try:
        task_state = await runtime.redis_client.hgetall(app_module.task_state_key(task_id))
    except Exception:
        return _service_degraded()

    if not task_state:
        return None

    task_user_id = task_state.get("user_id")
    if task_user_id is None or not _authorized_for_user(
        app_module,
        current_user=current_user,
        task_user_id=task_user_id,
    ):
        return _task_not_found()

    status = task_state.get("status", app_module.DEFAULT_TASK_STATUS)
    if (
        status in app_module.TASK_TERMINAL_STATUSES
        and task_state.get("result") is None
        and (task_state.get("error") is None or task_state.get("error") == "")
    ):
        return None

    response = _task_state_response(
        app_module,
        task_id=task_id,
        task_state=task_state,
        task_result_ttl_seconds=runtime.settings.task_result_ttl_seconds,
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


async def _poll_from_query_view(
    app_module: TaskReadRoutesApp,
    *,
    runtime: RuntimeState,
    task_id: UUID,
    current_user: AuthUser,
) -> JSONResponse | None:
    try:
        task = await app_module.get_task_query_view(runtime.db_pool, task_id)
    except Exception:
        return _service_degraded()

    if task is None:
        return None
    if not _authorized_for_user(
        app_module,
        current_user=current_user,
        task_user_id=str(task.user_id),
    ):
        return _task_not_found()

    response = _projection_response(
        app_module,
        task=task,
        ttl_seconds=runtime.settings.task_result_ttl_seconds,
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


async def _poll_from_command(
    app_module: TaskReadRoutesApp,
    *,
    runtime: RuntimeState,
    task_id: UUID,
    current_user: AuthUser,
) -> JSONResponse:
    try:
        task = await app_module.get_task_command(runtime.db_pool, task_id)
    except Exception:
        return _service_degraded()

    if task is None:
        return _task_not_found()
    if not _authorized_for_user(
        app_module,
        current_user=current_user,
        task_user_id=str(task.user_id),
    ):
        return _task_not_found()

    response = _command_response(task=task, ttl_seconds=runtime.settings.task_result_ttl_seconds)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


async def _poll_task(
    app_module: TaskReadRoutesApp,
    *,
    task_id: UUID,
    request: Request,
    current_user: AuthUser,
) -> JSONResponse:
    app_module._require_scopes(
        current_user=current_user,
        required_scopes=frozenset({OAuthScope.TASK_POLL.value}),
    )
    runtime = app_module._runtime_state(request)

    response = await _poll_from_task_state(
        app_module,
        runtime=runtime,
        task_id=task_id,
        current_user=current_user,
    )
    if response is not None:
        return response

    response = await _poll_from_query_view(
        app_module,
        runtime=runtime,
        task_id=task_id,
        current_user=current_user,
    )
    if response is not None:
        return response

    return await _poll_from_command(
        app_module,
        runtime=runtime,
        task_id=task_id,
        current_user=current_user,
    )


def register_task_read_routes(app: FastAPI, app_module: TaskReadRoutesApp) -> None:
    """Register task query routes."""

    @app.get(COMPAT_TASK_POLL_PATH, response_model=PollTaskResponse, tags=["compat"])
    @app.get(V1_TASK_POLL_PATH, response_model=PollTaskResponse)
    async def poll_task(
        task_id: UUID,
        request: Request,
        current_user: AuthUser = Depends(app_module._authenticate),
    ) -> JSONResponse:
        """Fetch task status/result for the caller if authorized."""
        return await _poll_task(
            app_module,
            task_id=task_id,
            request=request,
            current_user=current_user,
        )
