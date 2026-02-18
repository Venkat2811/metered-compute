"""Typed protocol contracts for route modules that consume `solution1.app` symbols."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

import asyncpg
from fastapi import Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from solution1.constants import TaskStatus
from solution1.core.dependencies import DependencyHealthService
from solution1.core.runtime import RuntimeState
from solution1.models.domain import (
    AdmissionDecision,
    AuthUser,
    TaskRecord,
    WebhookSubscription,
)


class _LoggerLike(Protocol):
    def info(self, event: str, /, **kwargs: object) -> object: ...

    def warning(self, event: str, /, **kwargs: object) -> object: ...

    def exception(self, event: str, /, **kwargs: object) -> object: ...


class _CounterLike(Protocol):
    def inc(self, amount: float = 1.0) -> None: ...


class _LabelCounterLike(Protocol):
    def labels(self, *args: object, **kwargs: object) -> _CounterLike: ...


class _GaugeLike(Protocol):
    def set(self, value: float) -> None: ...


class AdminRoutesApp(Protocol):
    ADMIN_ROLE: str
    CREDIT_DEDUCTIONS_TOTAL: _LabelCounterLike
    logger: _LoggerLike

    async def _authenticate(self, request: Request) -> AuthUser: ...

    def _require_scopes(
        self, *, current_user: AuthUser, required_scopes: frozenset[str]
    ) -> None: ...

    def _runtime_state(self, request: Request) -> RuntimeState: ...

    def _error_response(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> JSONResponse: ...

    async def admin_update_user_credits(
        self,
        pool: asyncpg.Pool,
        *,
        target_api_key: str,
        delta: int,
        reason: str,
    ) -> tuple[UUID, int] | None: ...

    def credits_cache_key(self, user_id: UUID) -> str: ...

    async def invalidate_user_auth_cache(
        self, *, api_key: str, redis_client: Redis[str]
    ) -> None: ...


class TaskReadRoutesApp(Protocol):
    ADMIN_ROLE: str
    DEFAULT_TASK_STATUS: str
    TASK_RUNNING_STATUSES: frozenset[str]
    TASK_TERMINAL_STATUSES: frozenset[str]
    TaskStatus: type[TaskStatus]
    STREAM_QUEUE_DEPTH: _GaugeLike

    async def _authenticate(self, request: Request) -> AuthUser: ...

    def _require_scopes(
        self, *, current_user: AuthUser, required_scopes: frozenset[str]
    ) -> None: ...

    def _runtime_state(self, request: Request) -> RuntimeState: ...

    def _error_response(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> JSONResponse: ...

    async def get_task(self, executor: asyncpg.Pool, task_id: UUID) -> TaskRecord | None: ...

    async def update_task_expired(self, executor: asyncpg.Pool, *, task_id: UUID) -> None: ...

    def _task_expires_at(self, task: TaskRecord, ttl_seconds: int) -> datetime: ...

    def result_cache_key(self, task_id: UUID) -> str: ...

    def task_state_key(self, task_id: UUID) -> str: ...


class TaskWriteRoutesApp(Protocol):
    ADMIN_ROLE: str
    DEFAULT_TASK_STATUS: str
    TASK_CANCELLABLE_STATUSES: frozenset[str]
    TaskStatus: type[TaskStatus]
    CREDIT_DEDUCTIONS_TOTAL: _LabelCounterLike
    TASK_SUBMISSIONS_TOTAL: _LabelCounterLike
    logger: _LoggerLike
    _TaskCancellationConflict: type[Exception]

    async def _authenticate(self, request: Request) -> AuthUser: ...

    def _require_scopes(
        self, *, current_user: AuthUser, required_scopes: frozenset[str]
    ) -> None: ...

    def _runtime_state(self, request: Request) -> RuntimeState: ...

    def _error_response(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> JSONResponse: ...

    def _task_expires_at(self, task: TaskRecord, ttl_seconds: int) -> datetime: ...

    async def run_admission_gate(
        self,
        *,
        redis_client: Redis[str],
        admission_script_sha: str,
        user_id: UUID,
        task_id: UUID,
        cost: int,
        idempotency_value: str,
        idempotency_ttl_seconds: int,
        max_concurrent: int,
        stream_key: str = "tasks:stream",
        task_ttl_seconds: int = 86_400,
        stream_payload: dict[str, object] | None = None,
        stream_maxlen: int = 500_000,
    ) -> tuple[AdmissionDecision, str]: ...

    async def hydrate_credits_from_db(
        self,
        *,
        redis_client: Redis[str],
        db_pool: asyncpg.Pool,
        api_key: str,
        user_id: UUID,
    ) -> bool: ...

    async def create_task_record(
        self,
        executor: asyncpg.Connection,
        *,
        task_id: UUID,
        api_key: str,
        user_id: UUID,
        x: int,
        y: int,
        cost: int,
        idempotency_key: str | None,
    ) -> None: ...

    async def insert_credit_transaction(
        self,
        executor: asyncpg.Connection,
        *,
        user_id: UUID,
        task_id: UUID | None,
        delta: int,
        reason: str,
    ) -> None: ...

    async def get_task(self, executor: asyncpg.Pool, task_id: UUID) -> TaskRecord | None: ...

    async def refund_and_decrement_active(
        self,
        *,
        redis_client: Redis[str],
        decrement_script_sha: str,
        user_id: UUID,
        amount: int,
    ) -> str: ...

    def idempotency_key(self, user_id: UUID, value: str) -> str: ...

    def pending_marker_key(self, task_id: UUID) -> str: ...

    def task_state_key(self, task_id: UUID) -> str: ...

    async def update_task_failed(
        self,
        executor: asyncpg.Connection,
        *,
        task_id: UUID,
        error: str,
    ) -> bool: ...

    async def update_task_cancelled(
        self, executor: asyncpg.Connection, *, task_id: UUID
    ) -> bool: ...


class SystemRoutesApp(Protocol):
    def _runtime_state(self, request: Request) -> RuntimeState: ...

    def _health_service(self, request: Request) -> DependencyHealthService: ...

    async def _check_worker_connectivity(
        self,
        *,
        redis_client: Redis[str],
        heartbeat_key: str,
        timeout_seconds: float = 1.0,
    ) -> bool: ...


class WebhookRoutesApp(Protocol):
    logger: _LoggerLike

    async def _authenticate(self, request: Request) -> AuthUser: ...

    def _require_scopes(
        self, *, current_user: AuthUser, required_scopes: frozenset[str]
    ) -> None: ...

    def _runtime_state(self, request: Request) -> RuntimeState: ...

    def _error_response(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retry_after: int | None = None,
    ) -> JSONResponse: ...

    async def upsert_webhook_subscription(
        self,
        pool: asyncpg.Pool,
        *,
        user_id: UUID,
        callback_url: str,
        enabled: bool = True,
    ) -> WebhookSubscription: ...

    async def get_webhook_subscription(
        self, pool: asyncpg.Pool, *, user_id: UUID
    ) -> WebhookSubscription | None: ...

    async def disable_webhook_subscription(
        self, pool: asyncpg.Pool, *, user_id: UUID
    ) -> WebhookSubscription | None: ...
