"""Read-only task APIs (poll/status) with cache-first lookup."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from solution0.api.contracts import TaskReadRoutesApp
from solution0.api.paths import COMPAT_TASK_POLL_PATH, V1_TASK_POLL_PATH
from solution0.models.domain import AuthUser
from solution0.models.schemas import PollTaskResponse


def _error_response(
    app_module: TaskReadRoutesApp,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    """Return the shared API error envelope as a typed JSON response."""
    return app_module._error_response(status_code=status_code, code=code, message=message)


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
        runtime = app_module._runtime_state(request)

        try:
            cached = await runtime.redis_client.hgetall(app_module.result_cache_key(task_id))
        except Exception:
            return _error_response(
                app_module,
                status_code=503,
                code="SERVICE_DEGRADED",
                message="Service temporarily unavailable",
            )
        if cached:
            cached_user_id = cached.get("user_id")
            if current_user.role != app_module.ADMIN_ROLE and cached_user_id != str(
                current_user.user_id
            ):
                return _error_response(
                    app_module, status_code=404, code="NOT_FOUND", message="Task not found"
                )

            response = PollTaskResponse(
                task_id=task_id,
                status=cached.get("status", app_module.DEFAULT_TASK_STATUS),
                result=json.loads(cached["result"]) if cached.get("result") else None,
                error=cached.get("error") or None,
                queue_position=int(cached["queue_position"])
                if cached.get("queue_position")
                else None,
                estimated_seconds=(
                    int(cached["estimated_seconds"]) if cached.get("estimated_seconds") else None
                ),
                expires_at=(
                    datetime.fromisoformat(cached["expires_at"])
                    if cached.get("expires_at")
                    else None
                ),
            )
            return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

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

        expires_at = app_module._task_expires_at(task, runtime.settings.task_result_ttl_seconds)
        status = task.status
        now = datetime.now(tz=UTC)
        if status in app_module.TASK_TERMINAL_STATUSES and now > expires_at:
            try:
                await app_module.update_task_expired(runtime.db_pool, task_id=task.task_id)
            except Exception:
                return _error_response(
                    app_module,
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Service temporarily unavailable",
                )
            status = app_module.TaskStatus.EXPIRED

        queue_position: int | None = None
        estimated_seconds: int | None = None
        if status in app_module.TASK_RUNNING_STATUSES:
            try:
                queue_depth = int(
                    await runtime.redis_client.llen(runtime.settings.celery_queue_name)
                )
            except Exception:
                return _error_response(
                    app_module,
                    status_code=503,
                    code="SERVICE_DEGRADED",
                    message="Service temporarily unavailable",
                )
            app_module.CELERY_QUEUE_DEPTH.set(queue_depth)
            queue_position = queue_depth
            estimated_seconds = queue_depth * 2

        response = PollTaskResponse(
            task_id=task.task_id,
            status=status,
            result=task.result,
            error=task.error,
            queue_position=queue_position,
            estimated_seconds=estimated_seconds,
            expires_at=expires_at,
        )
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
