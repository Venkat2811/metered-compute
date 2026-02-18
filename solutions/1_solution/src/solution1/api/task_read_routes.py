"""Read-only task APIs (poll/status) with cache-first lookup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from solution1.api.contracts import TaskReadRoutesApp
from solution1.api.error_responses import api_error_response
from solution1.api.paths import COMPAT_TASK_POLL_PATH, V1_TASK_POLL_PATH
from solution1.constants import OAuthScope
from solution1.core.runtime import RuntimeState
from solution1.models.domain import AuthUser, TaskRecord
from solution1.models.schemas import PollTaskResponse


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


async def _queue_estimate(
    app_module: TaskReadRoutesApp,
    *,
    runtime: RuntimeState,
    status: str,
) -> tuple[int | None, int | None]:
    if status not in app_module.TASK_RUNNING_STATUSES:
        return None, None

    queue_depth = int(await runtime.redis_client.xlen(runtime.settings.redis_tasks_stream_key))
    app_module.STREAM_QUEUE_DEPTH.set(queue_depth)
    return queue_depth, queue_depth * 2


def _cached_response(
    app_module: TaskReadRoutesApp,
    *,
    task_id: UUID,
    cached: dict[str, str],
) -> PollTaskResponse:
    return PollTaskResponse(
        task_id=task_id,
        status=cached.get("status", app_module.DEFAULT_TASK_STATUS),
        result=json.loads(cached["result"]) if cached.get("result") else None,
        error=cached.get("error") or None,
        queue_position=int(cached["queue_position"]) if cached.get("queue_position") else None,
        estimated_seconds=(
            int(cached["estimated_seconds"]) if cached.get("estimated_seconds") else None
        ),
        expires_at=(
            datetime.fromisoformat(cached["expires_at"]) if cached.get("expires_at") else None
        ),
    )


def _task_state_expires_at(task_state: dict[str, str], ttl_seconds: int) -> datetime | None:
    created_at_epoch_raw = task_state.get("created_at_epoch")
    if not created_at_epoch_raw or not created_at_epoch_raw.isdigit():
        return None

    created_at = datetime.fromtimestamp(int(created_at_epoch_raw), tz=UTC)
    return created_at + timedelta(seconds=ttl_seconds)


def _task_state_response(
    app_module: TaskReadRoutesApp,
    *,
    task_id: UUID,
    task_state: dict[str, str],
    queue_position: int | None,
    estimated_seconds: int | None,
    task_result_ttl_seconds: int,
) -> PollTaskResponse:
    return PollTaskResponse(
        task_id=task_id,
        status=task_state.get("status", app_module.DEFAULT_TASK_STATUS),
        result=None,
        error=None,
        queue_position=queue_position,
        estimated_seconds=estimated_seconds,
        expires_at=_task_state_expires_at(task_state, task_result_ttl_seconds),
    )


def _task_record_response(
    task: TaskRecord,
    *,
    status: str,
    queue_position: int | None,
    estimated_seconds: int | None,
    expires_at: datetime,
) -> PollTaskResponse:
    return PollTaskResponse(
        task_id=task.task_id,
        status=status,
        result=task.result,
        error=task.error,
        queue_position=queue_position,
        estimated_seconds=estimated_seconds,
        expires_at=expires_at,
    )


async def _poll_from_result_cache(
    app_module: TaskReadRoutesApp,
    *,
    runtime: RuntimeState,
    task_id: UUID,
    current_user: AuthUser,
) -> JSONResponse | None:
    try:
        cached = await runtime.redis_client.hgetall(app_module.result_cache_key(task_id))
    except Exception:
        return _service_degraded()

    if not cached:
        return None

    cached_user_id = cached.get("user_id")
    if cached_user_id is None or not _authorized_for_user(
        app_module,
        current_user=current_user,
        task_user_id=cached_user_id,
    ):
        return _task_not_found()

    response = _cached_response(app_module, task_id=task_id, cached=cached)
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


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
    try:
        queue_position, estimated_seconds = await _queue_estimate(
            app_module,
            runtime=runtime,
            status=status,
        )
    except Exception:
        return _service_degraded()

    if status in app_module.TASK_TERMINAL_STATUSES:
        try:
            terminal_task = await app_module.get_task(runtime.db_pool, task_id)
        except Exception:
            return _service_degraded()
        if terminal_task is not None and _authorized_for_user(
            app_module,
            current_user=current_user,
            task_user_id=str(terminal_task.user_id),
        ):
            expires_at = app_module._task_expires_at(
                terminal_task, runtime.settings.task_result_ttl_seconds
            )
            terminal_status = str(terminal_task.status)
            if (
                terminal_status in app_module.TASK_TERMINAL_STATUSES
                and datetime.now(tz=UTC) > expires_at
            ):
                try:
                    await app_module.update_task_expired(
                        runtime.db_pool, task_id=terminal_task.task_id
                    )
                except Exception:
                    return _service_degraded()
                terminal_status = app_module.TaskStatus.EXPIRED

            response = _task_record_response(
                terminal_task,
                status=terminal_status,
                queue_position=queue_position,
                estimated_seconds=estimated_seconds,
                expires_at=expires_at,
            )
            return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    response = _task_state_response(
        app_module,
        task_id=task_id,
        task_state=task_state,
        queue_position=queue_position,
        estimated_seconds=estimated_seconds,
        task_result_ttl_seconds=runtime.settings.task_result_ttl_seconds,
    )
    return JSONResponse(status_code=200, content=response.model_dump(mode="json"))


async def _poll_from_db(
    app_module: TaskReadRoutesApp,
    *,
    runtime: RuntimeState,
    task_id: UUID,
    current_user: AuthUser,
) -> JSONResponse:
    try:
        task = await app_module.get_task(runtime.db_pool, task_id)
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

    expires_at = app_module._task_expires_at(task, runtime.settings.task_result_ttl_seconds)
    status = task.status
    if status in app_module.TASK_TERMINAL_STATUSES and datetime.now(tz=UTC) > expires_at:
        try:
            await app_module.update_task_expired(runtime.db_pool, task_id=task.task_id)
        except Exception:
            return _service_degraded()
        status = app_module.TaskStatus.EXPIRED

    try:
        queue_position, estimated_seconds = await _queue_estimate(
            app_module,
            runtime=runtime,
            status=str(status),
        )
    except Exception:
        return _service_degraded()

    response = _task_record_response(
        task,
        status=str(status),
        queue_position=queue_position,
        estimated_seconds=estimated_seconds,
        expires_at=expires_at,
    )
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

    response = await _poll_from_result_cache(
        app_module,
        runtime=runtime,
        task_id=task_id,
        current_user=current_user,
    )
    if response is not None:
        return response

    response = await _poll_from_task_state(
        app_module,
        runtime=runtime,
        task_id=task_id,
        current_user=current_user,
    )
    if response is not None:
        return response

    return await _poll_from_db(
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
