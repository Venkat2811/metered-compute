"""FastAPI application — routes, auth, lifespan.

TigerBeetle handles all billing. Restate handles durable execution.
This file wires them together with a thin API layer.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

import asyncpg
import httpx
import redis.asyncio as aioredis
import restate
import structlog
import tigerbeetle as tb
import uuid6
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest
from pydantic import BaseModel

from solution5 import cache, metrics, repository
from solution5.billing import Billing
from solution5.logging import setup_logging
from solution5.settings import Settings
from solution5.workflows import _state as workflow_state
from solution5.workflows import task_service

log = structlog.get_logger()


# ── Request / Response models ──────────────────────────────────────


class SubmitRequest(BaseModel):
    x: int
    y: int
    idempotency_key: str | None = None


class SubmitResponse(BaseModel):
    task_id: str
    status: str


class CancelResponse(BaseModel):
    task_id: str
    status: str
    credits_refunded: int


class AdminCreditsRequest(BaseModel):
    user_id: str
    amount: int


class AdminCreditsResponse(BaseModel):
    user_id: str
    new_balance: int


def _auth_payload_from_cache(payload: dict[str, str] | None) -> dict[str, str] | None:
    if not payload:
        return None

    role = payload.get("role")
    user_id = payload.get("user_id")
    if not role or not user_id:
        # If cache was created before role became part of the auth payload,
        # fall back to DB lookup to avoid stale privilege information.
        return None

    return {
        "user_id": user_id,
        "name": payload.get("name", ""),
        "role": role,
    }


async def _is_restate_service_registered(admin_url: str, service_name: str = "TaskService") -> bool:
    """Return True when Restate reports the deployment for the expected service."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{admin_url}/deployments", timeout=5)
        if resp.status_code != 200:
            return False

        payload = resp.json()
        deployments = payload.get("deployments", [])
        return any(
            any(service.get("name") == service_name for service in deployment.get("services", []))
            for deployment in deployments
        )


async def _register_restate_service(
    admin_url: str,
    restate_uri: str,
    *,
    max_attempts: int = 12,
) -> bool:
    """Register the API container as a Restate deployment and wait until service is visible."""
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{admin_url}/deployments",
                    json={"uri": restate_uri, "use_http_11": True},
                    headers={"content-type": "application/json"},
                    timeout=10,
                )
                if response.status_code in (200, 201, 409) and await _is_restate_service_registered(admin_url):
                    return True
        except Exception as exc:
            log.warning(
                "restate_registration_attempt_failed",
                attempt=attempt,
                error=str(exc),
            )

        await asyncio.sleep(1)

    return False


async def _ensure_restate_registration(
    app: FastAPI,
    settings: Settings,
) -> bool:
    """Ensure Restate is registered and usable, with in-process synchronization."""
    # Fast path.
    if getattr(app.state, "restate_ready", False):
        return True

    async with app.state.restate_registration_lock:
        if app.state.restate_ready:
            return True

        ready = await _register_restate_service(
            admin_url=settings.restate_admin_url,
            restate_uri="http://api:8000/restate",
            max_attempts=12,
        )
        app.state.restate_ready = ready
        app.state._restate_state["restate_ready"] = ready
        workflow_state["restate_ready"] = ready
        if ready:
            log.info("restate_registered")
        else:
            log.error("restate_registration_failed")
        return ready


# ── Application factory ───────────────────────────────────────────


def create_app() -> FastAPI:
    setup_logging()
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # ── Postgres ──
        pg_pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=2, max_size=10)
        await repository.run_migrations(pg_pool)

        # ── Redis ──
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=False)

        # ── TigerBeetle ──
        # TB client requires numeric IP addresses, not hostnames
        tb_addr = settings.tigerbeetle_addresses
        if ":" in tb_addr:
            host, port = tb_addr.rsplit(":", 1)
            if not host[0].isdigit():
                host = socket.gethostbyname(host)
            tb_addr = f"{host}:{port}"
        tb_client = tb.client.ClientSync(
            cluster_id=settings.tigerbeetle_cluster_id,
            replica_addresses=tb_addr,
        )
        billing = Billing(
            client=tb_client,
            revenue_id=settings.tb_revenue_account_id,
            escrow_id=settings.tb_escrow_account_id,
            timeout_secs=settings.tb_transfer_timeout_secs,
        )
        billing.ensure_platform_accounts()

        # Seed TB user accounts from PG
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id, credits FROM users")
            for row in rows:
                uid = str(row["user_id"])
                billing.ensure_user_account(uid)
                if billing.get_balance(uid) == 0 and row["credits"] > 0:
                    tid = uuid6.uuid7().hex
                    billing.topup_credits(uid, int(tid, 16), row["credits"])

        # ── Share state with Restate workflow handlers ──
        shared: dict[str, Any] = {
            "pg_pool": pg_pool,
            "redis": redis_conn,
            "billing": billing,
            "settings": settings,
            "restate_ready": False,
        }
        workflow_state.update(shared)
        app.state.pg_pool = pg_pool
        app.state.redis = redis_conn
        app.state.billing = billing
        app.state.settings = settings
        app.state.restate_ready = False
        app.state._restate_state = shared
        app.state.restate_registration_lock = asyncio.Lock()

        async def _async_register_restate() -> None:
            try:
                await _ensure_restate_registration(app, settings)
            except Exception:
                log.exception("restate_registration_task_failed")

        app.state.restate_registration_task = asyncio.create_task(_async_register_restate())

        yield

        registration_task = getattr(app.state, "restate_registration_task", None)
        if registration_task is not None and not registration_task.done():
            registration_task.cancel()
            with suppress(asyncio.CancelledError):
                await registration_task

        await pg_pool.close()
        await redis_conn.aclose()

    app = FastAPI(title="Solution 4 — TB + Restate", lifespan=lifespan)

    # Mount Restate service endpoint as ASGI sub-app
    restate_app = restate.app(services=[task_service])
    app.mount("/restate", restate_app)

    # ── Auth helper ────────────────────────────────────────────────

    async def authenticate(request: Request) -> dict[str, str]:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(401, "Missing API key")
        api_key = auth[7:]

        cached = await cache.get_cached_auth(request.app.state.redis, api_key)
        normalized_cached = _auth_payload_from_cache(cached)
        if normalized_cached:
            return normalized_cached

        user = await repository.get_user_by_api_key(request.app.state.pg_pool, api_key)
        if not user:
            raise HTTPException(401, "Invalid API key")

        user_dict = {
            "user_id": str(user["user_id"]),
            "name": user["name"],
            "role": str(user.get("role", "user")),
        }
        await cache.cache_auth(request.app.state.redis, api_key, user_dict)
        return user_dict

    def _ensure_admin(user: dict[str, str]) -> None:
        if user.get("role") != "admin":
            raise HTTPException(403, "Admin role required")

    # ── Routes ─────────────────────────────────────────────────────

    @app.post("/v1/task", response_model=SubmitResponse, status_code=201)
    async def submit_task(body: SubmitRequest, request: Request) -> SubmitResponse | JSONResponse:
        user = await authenticate(request)
        user_id = user["user_id"]
        billing: Billing = request.app.state.billing
        stg: Settings = request.app.state.settings

        if not request.app.state.restate_ready and not await _ensure_restate_registration(request.app, stg):
            raise HTTPException(503, "Execution control plane is not ready")

        task_id = str(uuid6.uuid7())
        transfer_hex = uuid6.uuid7().hex
        transfer_int = int(transfer_hex, 16)
        cost = stg.default_task_cost

        billing.ensure_user_account(user_id)

        if not billing.reserve_credits(user_id, transfer_int, cost):
            raise HTTPException(402, "Insufficient credits")
        metrics.TASK_SUBMITTED.labels(status="accepted").inc()

        try:
            task = await repository.create_task(
                request.app.state.pg_pool,
                task_id=task_id,
                user_id=user_id,
                x=body.x,
                y=body.y,
                cost=cost,
                tb_transfer_id=transfer_hex,
                idempotency_key=body.idempotency_key,
            )
        except Exception as exc:
            billing.release_credits(transfer_int)
            raise HTTPException(500, "Task creation failed") from exc

        # Idempotency replay: create_task returned existing row
        existing_id = str(task["task_id"])
        if existing_id != task_id:
            billing.release_credits(transfer_int)
            return JSONResponse(
                content=SubmitResponse(task_id=existing_id, status=task["status"]).model_dump(),
                status_code=200,
            )

        await cache.cache_task(request.app.state.redis, task_id, task)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{stg.restate_ingress_url}/TaskService/execute_task/send",
                    json={
                        "task_id": task_id,
                        "tb_transfer_id": transfer_hex,
                        "x": body.x,
                        "y": body.y,
                    },
                    headers={"idempotency-key": task_id},
                    timeout=5,
                )
                if response.status_code >= 400:
                    raise HTTPException(
                        503,
                        f"Execution orchestration failed: {response.status_code}",
                    )
        except HTTPException:
            # Keep financial state consistent if Restate invocation could not be started.
            billing.release_credits(transfer_int)
            await repository.update_task_status(
                request.app.state.pg_pool,
                task_id,
                "FAILED",
            )
            await cache.invalidate_task(request.app.state.redis, task_id)
            raise
        except Exception as e:
            log.warning("restate_invoke_failed", task_id=task_id, error=str(e))
            billing.release_credits(transfer_int)
            await repository.update_task_status(
                request.app.state.pg_pool,
                task_id,
                "FAILED",
            )
            await cache.invalidate_task(request.app.state.redis, task_id)
            raise HTTPException(
                503,
                "Execution orchestration unavailable. Credits have been released.",
            ) from e

        return SubmitResponse(task_id=task_id, status="PENDING")

    @app.get("/v1/poll")
    async def poll_task(task_id: str, request: Request) -> dict[str, str]:
        user = await authenticate(request)

        cached = await cache.get_cached_task(request.app.state.redis, task_id)
        if cached:
            if cached.get("user_id") != user["user_id"]:
                raise HTTPException(403, "Not your task")
            return cached

        task = await repository.get_task(request.app.state.pg_pool, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        if str(task["user_id"]) != user["user_id"]:
            raise HTTPException(403, "Not your task")

        task_dict = {k: str(v) for k, v in task.items() if v is not None}
        await cache.cache_task(request.app.state.redis, task_id, task_dict)
        return task_dict

    @app.post("/v1/task/{task_id}/cancel", response_model=CancelResponse)
    async def cancel_task(task_id: str, request: Request) -> CancelResponse:
        user = await authenticate(request)
        billing: Billing = request.app.state.billing

        task = await repository.get_task(request.app.state.pg_pool, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        if str(task["user_id"]) != user["user_id"]:
            raise HTTPException(403, "Not your task")
        if task["status"] != "PENDING":
            raise HTTPException(409, f"Cannot cancel task in {task['status']} state")

        cancelled = await repository.update_task_status_if_match(
            request.app.state.pg_pool,
            task_id,
            status="CANCELLED",
            expected_status="PENDING",
        )
        if not cancelled:
            raise HTTPException(409, "Task state changed while cancel was processed")

        transfer_int = int(task["tb_transfer_id"], 16)
        if not billing.release_credits(transfer_int):
            await repository.update_task_status_if_match(
                request.app.state.pg_pool,
                task_id,
                status="PENDING",
                expected_status="CANCELLED",
            )
            raise HTTPException(500, "Credit release failed")
        metrics.TASK_CANCELLED.inc()
        await cache.invalidate_task(request.app.state.redis, task_id)

        return CancelResponse(task_id=task_id, status="CANCELLED", credits_refunded=task["cost"])

    @app.post("/v1/admin/credits", response_model=AdminCreditsResponse)
    async def admin_credits(body: AdminCreditsRequest, request: Request) -> AdminCreditsResponse:
        user = await authenticate(request)
        _ensure_admin(user)
        billing: Billing = request.app.state.billing

        billing.ensure_user_account(body.user_id)
        transfer_int = int(uuid6.uuid7().hex, 16)
        if not billing.topup_credits(body.user_id, transfer_int, body.amount):
            raise HTTPException(500, "Topup failed")

        new_balance = billing.get_balance(body.user_id)
        await repository.update_user_credits(request.app.state.pg_pool, body.user_id, new_balance)
        return AdminCreditsResponse(user_id=body.user_id, new_balance=new_balance)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            await request.app.state.pg_pool.fetchval("SELECT 1")
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "error"
        try:
            await request.app.state.redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"
        try:
            if request.app.state.billing.is_ready():
                checks["tigerbeetle"] = "ok"
            else:
                checks["tigerbeetle"] = "error"
        except Exception:
            checks["tigerbeetle"] = "error"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{request.app.state.settings.restate_admin_url}/health")
                checks["restate"] = "ok" if resp.status_code == 200 else "error"
        except Exception:
            checks["restate"] = "error"

        checks["restate_service"] = "ok" if getattr(request.app.state, "restate_ready", False) else "error"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{request.app.state.settings.compute_worker_url}/health")
                checks["compute"] = "ok" if resp.status_code == 200 else "error"
        except Exception:
            checks["compute"] = "error"

        all_ok = all(v == "ok" for v in checks.values())
        return JSONResponse(checks, status_code=200 if all_ok else 503)

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type="text/plain; charset=utf-8")

    return app


async def _run_migrations() -> None:
    """Standalone migration runner for Makefile."""
    settings = Settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=2)
    await repository.run_migrations(pool)
    await pool.close()
