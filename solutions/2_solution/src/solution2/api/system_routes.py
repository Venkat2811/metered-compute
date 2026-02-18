"""System endpoints for health/readiness/metrics and assignment hit counter."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from solution2.api.contracts import SystemRoutesApp
from solution2.api.paths import HEALTH_PATH, HIT_PATH, METRICS_PATH, READY_PATH
from solution2.models.schemas import ReadyResponse


def register_system_routes(app: FastAPI, app_module: SystemRoutesApp) -> None:
    """Register operational endpoints used by probes and demos."""

    @app.get(HEALTH_PATH)
    async def health() -> dict[str, str]:
        """Return liveness status only."""
        return {"status": "ok", "service": "mc-solution2-api"}

    @app.get(READY_PATH, response_model=ReadyResponse)
    async def ready(request: Request) -> JSONResponse:
        """Return dependency-level readiness."""
        readiness = await app_module._health_service(request).readiness()
        dependencies = dict(readiness.dependencies)
        dependencies.setdefault("worker", True)
        dependencies.setdefault("redis_scripts", True)
        overall_ready = bool(readiness.ready)
        payload = ReadyResponse(
            ready=overall_ready,
            dependencies=dependencies,
            trace_id=str(uuid.uuid4()),
        )
        status_code = 200 if overall_ready else 503
        return JSONResponse(status_code=status_code, content=payload.model_dump())

    @app.get(METRICS_PATH)
    async def metrics() -> Response:
        """Expose Prometheus metrics."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get(HIT_PATH)
    async def hit(request: Request) -> dict[str, str]:
        """Increment and return demo hit counter from Redis."""
        runtime = app_module._runtime_state(request)
        count = await runtime.redis_client.incr("hits")
        return {"message": f"Hello World! I have been seen {count} times."}
