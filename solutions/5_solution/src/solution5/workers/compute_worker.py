"""Standalone compute worker service (external to Restate control plane)."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ComputeRequest(BaseModel):
    task_id: str
    x: int
    y: int
    user_id: str | None = None
    model_class: str | None = None

    class Config:
        extra = "ignore"


@dataclass(frozen=True)
class _CachedCompute:
    result: dict[str, int]
    cached_at: float


_CACHE_TTL_SECONDS = 3600
_cache: dict[str, _CachedCompute] = {}
_cache_lock = asyncio.Lock()


def _compute_payload(x: int, y: int) -> dict[str, int]:
    key = f"{x}:{y}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    _ = int(digest, 16)  # retained for traceability in logs
    # Keep compute visible but cheap enough for this showcase.
    return {"sum": x + y, "product": x * y}


async def _compute_with_delay(x: int, y: int) -> dict[str, int]:
    await asyncio.sleep(0.5)
    return _compute_payload(x, y)


def _is_cached(task_id: str) -> dict[str, int] | None:
    cached = _cache.get(task_id)
    if cached is None:
        return None
    if time.time() - cached.cached_at > _CACHE_TTL_SECONDS:
        _cache.pop(task_id, None)
        return None
    return cached.result


async def _store_cache(task_id: str, result: dict[str, int]) -> None:
    async with _cache_lock:
        existing = _cache.get(task_id)
        if existing is not None and time.time() - existing.cached_at <= _CACHE_TTL_SECONDS:
            return
        _cache[task_id] = _CachedCompute(result=result, cached_at=time.time())


def create_app() -> FastAPI:
    app = FastAPI(title="Solution 4 Compute Worker")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/compute")
    async def compute(request: ComputeRequest) -> JSONResponse:
        if request.task_id == "":
            return JSONResponse(
                status_code=422,
                content={"error": "task_id is required"},
            )
        cached = _is_cached(request.task_id)
        if cached is not None:
            return JSONResponse(status_code=200, content={"task_id": request.task_id, "result": cached})

        result = await _compute_with_delay(request.x, request.y)
        await _store_cache(request.task_id, result)
        return JSONResponse(status_code=200, content={"task_id": request.task_id, "result": result})

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
