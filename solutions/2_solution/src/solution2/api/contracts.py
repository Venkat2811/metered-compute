"""Typed protocol contracts for route modules that consume `solution2.app` symbols."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg
from fastapi import Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from solution2.constants import ModelClass, SubscriptionTier, TaskStatus
from solution2.core.dependencies import DependencyHealthService
from solution2.core.runtime import RuntimeState
from solution2.models.domain import (
    AdmissionDecision,
    AuthUser,
    CreditReservation,
    TaskCommand,
    TaskQueryView,
    WebhookSubscription,
)
from solution2.services.billing import (
    BatchAdmissionResult,
    BatchTaskSpec,
    SyncExecutionResult,
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
        executor: asyncpg.Connection | asyncpg.Pool,
        *,
        target_api_key: str,
        delta: int,
        reason: str,
    ) -> tuple[UUID, int, int] | None: ...

    async def create_outbox_event(
        self,
        executor: asyncpg.Connection | asyncpg.Pool,
        *,
        aggregate_id: UUID,
        event_type: str,
        routing_key: str,
        payload: dict[str, object],
    ) -> UUID: ...

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

    async def get_task_query_view(
        self,
        executor: asyncpg.Pool | asyncpg.Connection,
        task_id: UUID,
    ) -> TaskQueryView | None: ...

    async def get_task_command(
        self,
        executor: asyncpg.Pool | asyncpg.Connection,
        task_id: UUID,
    ) -> TaskCommand | None: ...

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

    async def run_admission_gate(
        self,
        *,
        admission_script_sha: str,
        user_id: UUID,
        task_id: UUID,
        cost: int,
        idempotency_value: str,
        max_concurrent: int,
        stream_payload: dict[str, object] | None = None,
        db_pool: asyncpg.Pool | None = None,
        request_mode: str | None = None,
        queue_name: str | None = None,
        reservation_ttl_seconds: int | None = None,
    ) -> tuple[AdmissionDecision, str]: ...

    async def run_batch_admission_gate(
        self,
        *,
        admission_script_sha: str,
        user_id: UUID,
        user_tier: SubscriptionTier,
        batch_id: UUID,
        tasks: tuple[BatchTaskSpec, ...],
        max_concurrent: int,
        base_task_cost: int,
        db_pool: asyncpg.Pool | None = None,
        reservation_ttl_seconds: int | None = None,
        trace_id: str | None = None,
    ) -> tuple[BatchAdmissionResult, str]: ...

    async def run_sync_submission(
        self,
        *,
        admission_script_sha: str,
        user_id: UUID,
        user_tier: SubscriptionTier,
        task_id: UUID,
        x: int,
        y: int,
        model_class: ModelClass,
        cost: int,
        callback_url: str | None,
        idempotency_value: str,
        max_concurrent: int,
        queue_name: str,
        execution_timeout_seconds: float,
        db_pool: asyncpg.Pool | None = None,
        reservation_ttl_seconds: int | None = None,
    ) -> tuple[AdmissionDecision, str, SyncExecutionResult | None]: ...

    async def insert_credit_transaction(
        self,
        executor: asyncpg.Connection,
        *,
        user_id: UUID,
        task_id: UUID | None,
        delta: int,
        reason: str,
    ) -> None: ...

    async def get_task_command(
        self,
        executor: asyncpg.Connection | asyncpg.Pool,
        task_id: UUID,
    ) -> TaskCommand | None: ...

    async def get_credit_reservation(
        self,
        executor: asyncpg.Connection | asyncpg.Pool,
        *,
        task_id: UUID,
        for_update: bool = False,
    ) -> CreditReservation | None: ...

    async def release_reservation(
        self,
        executor: asyncpg.Connection | asyncpg.Pool,
        *,
        task_id: UUID,
    ) -> bool: ...

    async def add_user_credits(
        self,
        executor: asyncpg.Connection | asyncpg.Pool,
        *,
        user_id: UUID,
        delta: int,
    ) -> int | None: ...

    async def create_outbox_event(
        self,
        executor: asyncpg.Connection | asyncpg.Pool,
        *,
        aggregate_id: UUID,
        event_type: str,
        routing_key: str,
        payload: dict[str, object],
    ) -> UUID: ...

    def task_state_key(self, task_id: UUID) -> str: ...

    async def update_task_command_cancelled(
        self, executor: asyncpg.Connection, *, task_id: UUID
    ) -> bool: ...


class SystemRoutesApp(Protocol):
    def _runtime_state(self, request: Request) -> RuntimeState: ...

    def _health_service(self, request: Request) -> DependencyHealthService: ...


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
