from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest
import tigerbeetle as tb

from solution3.constants import BillingState, ModelClass, RequestMode, SubscriptionTier, TaskStatus
from solution3.core.settings import AppSettings
from solution3.models.domain import ReconciledTaskState, StaleReservedTask
from solution3.services.billing import PendingTransferState
from solution3.workers import reconciler


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}
        self.decrements: list[str] = []
        self.set_calls: list[tuple[str, int]] = []

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes[key] = {**self.hashes.get(key, {}), **mapping}

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def decr(self, key: str) -> int:
        self.decrements.append(key)
        return 0

    async def set(self, key: str, value: int) -> None:
        self.set_calls.append((key, value))


class FakeBilling:
    def __init__(self, *states: PendingTransferState) -> None:
        self.states = list(states)
        self.calls: list[UUID] = []

    def get_pending_transfer_state(self, *, pending_transfer_id: UUID) -> PendingTransferState:
        self.calls.append(pending_transfer_id)
        if not self.states:
            raise AssertionError("unexpected billing lookup")
        return self.states.pop(0)


@dataclass(frozen=True, slots=True)
class _FakeState:
    task_id: UUID
    user_id: UUID
    status: TaskStatus
    billing_state: BillingState
    model_class: ModelClass


def _stale_task() -> StaleReservedTask:
    return StaleReservedTask(
        task_id=uuid4(),
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
        tb_pending_transfer_id=uuid4(),
        created_at=datetime.now(tz=UTC),
    )


def test_parse_args_accepts_override_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "reconciler",
            "--interval",
            "1.5",
            "--stale-after-seconds",
            "90",
            "--result-ttl-seconds",
            "600",
            "--once",
        ],
    )

    args = reconciler._parse_args()

    assert args.interval == 1.5
    assert args.stale_after_seconds == 90
    assert args.result_ttl_seconds == 600
    assert args.once is True


def test_install_stop_handlers_falls_back_to_signal_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[tuple[object, object]] = []

    class FakeLoop:
        def add_signal_handler(self, *_args: object, **_kwargs: object) -> None:
            raise NotImplementedError

    monkeypatch.setattr("solution3.workers.reconciler.asyncio.get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(
        "solution3.workers.reconciler.signal.signal",
        lambda sig, handler: registered.append((sig, handler)),
    )

    reconciler._install_stop_handlers(asyncio.Event())

    assert [sig for sig, _handler in registered] == [signal.SIGINT, signal.SIGTERM]


def test_terminal_alignment_target_returns_expected_mappings() -> None:
    assert reconciler._terminal_alignment_target(
        TaskStatus.PENDING,
        PendingTransferState.POSTED,
    ) == (TaskStatus.COMPLETED, BillingState.CAPTURED, "TB_CAPTURED")
    assert reconciler._terminal_alignment_target(
        TaskStatus.PENDING,
        PendingTransferState.VOIDED,
    ) == (TaskStatus.CANCELLED, BillingState.RELEASED, "TB_VOIDED")
    assert reconciler._terminal_alignment_target(
        TaskStatus.RUNNING,
        PendingTransferState.VOIDED,
    ) == (TaskStatus.FAILED, BillingState.RELEASED, "TB_VOIDED")
    assert (
        reconciler._terminal_alignment_target(TaskStatus.PENDING, PendingTransferState.ABSENT)
        is None
    )


def test_build_billing_uses_resolved_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client_calls: list[dict[str, object]] = []

    class FakeClient:
        def close(self) -> None:
            return None

    def fake_client_sync(*, cluster_id: int, replica_addresses: str) -> FakeClient:
        client_calls.append({"cluster_id": cluster_id, "replica_addresses": replica_addresses})
        return FakeClient()

    monkeypatch.setattr(
        reconciler,
        "resolve_tigerbeetle_addresses",
        lambda endpoint: f"resolved:{endpoint}",
    )
    monkeypatch.setattr(tb, "ClientSync", fake_client_sync)

    client, billing = reconciler._build_billing(
        cast(
            AppSettings,
            SimpleNamespace(
                tigerbeetle_endpoint="tigerbeetle:3000",
                tigerbeetle_cluster_id=7,
                tigerbeetle_ledger_id=11,
                tigerbeetle_revenue_account_id=12,
                tigerbeetle_escrow_account_id=13,
                tigerbeetle_pending_transfer_timeout_seconds=14,
            ),
        )
    )

    assert isinstance(client, FakeClient)
    assert client_calls == [{"cluster_id": 7, "replica_addresses": "resolved:tigerbeetle:3000"}]
    assert billing._ledger_id == 11
    assert billing._revenue_account_id == 12
    assert billing._escrow_account_id == 13
    assert billing._pending_timeout_seconds == 14


@pytest.mark.asyncio
async def test_reconcile_stale_reserved_tasks_expires_rows_and_updates_hot_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    redis = FakeRedis()
    expired_state = ReconciledTaskState(
        task_id=stale_task.task_id,
        user_id=stale_task.user_id,
        status=TaskStatus.EXPIRED,
        billing_state=BillingState.EXPIRED,
        model_class=stale_task.model_class,
    )

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    async def fake_expire(*_args: object, **kwargs: object) -> ReconciledTaskState | None:
        assert kwargs["task_id"] == stale_task.task_id
        assert kwargs["tb_pending_transfer_id"] == stale_task.tb_pending_transfer_id
        return expired_state

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "expire_stale_reserved_task", fake_expire)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(reconciler.ReconcilerRedis, redis),
        billing=cast(reconciler.ReconcilerBilling, FakeBilling(PendingTransferState.ABSENT)),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 1
    assert redis.decrements == [f"active:{stale_task.user_id}"]
    assert redis.hashes[f"task:{stale_task.task_id}"]["status"] == "EXPIRED"
    assert redis.hashes[f"task:{stale_task.task_id}"]["billing_state"] == "EXPIRED"
    assert redis.expirations[f"task:{stale_task.task_id}"] == 300


@pytest.mark.asyncio
async def test_reconcile_skips_rows_that_lose_the_update_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    redis = FakeRedis()

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    async def fake_expire(*_args: object, **_kwargs: object) -> ReconciledTaskState | None:
        return None

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "expire_stale_reserved_task", fake_expire)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(reconciler.ReconcilerRedis, redis),
        billing=cast(reconciler.ReconcilerBilling, FakeBilling(PendingTransferState.ABSENT)),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 0
    assert redis.hashes == {}


@pytest.mark.asyncio
async def test_reconcile_aligns_tb_posted_drift_to_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    redis = FakeRedis()
    billing = FakeBilling(PendingTransferState.POSTED)
    completed_state = ReconciledTaskState(
        task_id=stale_task.task_id,
        user_id=stale_task.user_id,
        status=TaskStatus.COMPLETED,
        billing_state=BillingState.CAPTURED,
        model_class=stale_task.model_class,
    )

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    align_calls: list[dict[str, object]] = []

    async def fake_align(*_args: object, **kwargs: object) -> ReconciledTaskState | None:
        align_calls.append(dict(kwargs))
        return completed_state

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "align_stale_reserved_task_terminal_state", fake_align)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(reconciler.ReconcilerRedis, redis),
        billing=cast(reconciler.ReconcilerBilling, billing),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 1
    assert billing.calls == [stale_task.tb_pending_transfer_id]
    assert align_calls == [
        {
            "task": stale_task,
            "status": TaskStatus.COMPLETED,
            "billing_state": BillingState.CAPTURED,
            "resolution": "TB_CAPTURED",
            "stale_after_seconds": 720,
        }
    ]
    assert redis.hashes[f"task:{stale_task.task_id}"]["status"] == "COMPLETED"
    assert redis.hashes[f"task:{stale_task.task_id}"]["billing_state"] == "CAPTURED"


@pytest.mark.asyncio
async def test_reconcile_aligns_tb_voided_pending_drift_to_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    redis = FakeRedis()
    billing = FakeBilling(PendingTransferState.VOIDED)
    cancelled_state = ReconciledTaskState(
        task_id=stale_task.task_id,
        user_id=stale_task.user_id,
        status=TaskStatus.CANCELLED,
        billing_state=BillingState.RELEASED,
        model_class=stale_task.model_class,
    )

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    align_calls: list[dict[str, object]] = []

    async def fake_align(*_args: object, **kwargs: object) -> ReconciledTaskState | None:
        align_calls.append(dict(kwargs))
        return cancelled_state

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "align_stale_reserved_task_terminal_state", fake_align)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(reconciler.ReconcilerRedis, redis),
        billing=cast(reconciler.ReconcilerBilling, billing),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 1
    assert align_calls[0]["status"] == TaskStatus.CANCELLED
    assert align_calls[0]["billing_state"] == BillingState.RELEASED
    assert align_calls[0]["resolution"] == "TB_VOIDED"
    assert redis.hashes[f"task:{stale_task.task_id}"]["status"] == "CANCELLED"
    assert redis.hashes[f"task:{stale_task.task_id}"]["billing_state"] == "RELEASED"


@pytest.mark.asyncio
async def test_reconcile_aligns_tb_voided_running_drift_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _stale_task()
    stale_task = StaleReservedTask(
        task_id=original.task_id,
        user_id=original.user_id,
        tier=original.tier,
        mode=original.mode,
        model_class=original.model_class,
        status=TaskStatus.RUNNING,
        billing_state=original.billing_state,
        tb_pending_transfer_id=original.tb_pending_transfer_id,
        created_at=original.created_at,
    )
    billing = FakeBilling(PendingTransferState.VOIDED)

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    align_calls: list[dict[str, object]] = []

    async def fake_align(*_args: object, **kwargs: object) -> ReconciledTaskState | None:
        align_calls.append(dict(kwargs))
        return None

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)
    monkeypatch.setattr(reconciler, "align_stale_reserved_task_terminal_state", fake_align)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=None,
        billing=cast(reconciler.ReconcilerBilling, billing),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 0
    assert align_calls[0]["status"] == TaskStatus.FAILED
    assert align_calls[0]["billing_state"] == BillingState.RELEASED


@pytest.mark.asyncio
async def test_reconcile_leaves_open_pending_transfer_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_task = _stale_task()
    billing = FakeBilling(PendingTransferState.PENDING)

    async def fake_list(*_args: object, **_kwargs: object) -> list[StaleReservedTask]:
        return [stale_task]

    monkeypatch.setattr(reconciler, "list_stale_reserved_tasks", fake_list)

    expire_called = False
    align_called = False

    async def fake_expire(*_args: object, **_kwargs: object) -> ReconciledTaskState | None:
        nonlocal expire_called
        expire_called = True
        return None

    async def fake_align(*_args: object, **_kwargs: object) -> ReconciledTaskState | None:
        nonlocal align_called
        align_called = True
        return None

    monkeypatch.setattr(reconciler, "expire_stale_reserved_task", fake_expire)
    monkeypatch.setattr(reconciler, "align_stale_reserved_task_terminal_state", fake_align)

    resolved = await reconciler.reconcile_stale_reserved_tasks(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=None,
        billing=cast(reconciler.ReconcilerBilling, billing),
        stale_after_seconds=720,
        result_ttl_seconds=300,
    )

    assert resolved == 0
    assert expire_called is False
    assert align_called is False


def test_main_uses_single_run_when_once_is_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[bool, int]] = []

    async def fake_main_async(
        *,
        once: bool,
        stale_after_seconds: int,
        **_kwargs: object,
    ) -> None:
        async_calls.append((once, stale_after_seconds))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    def fake_asyncio_run(coro: object) -> None:
        assert hasattr(coro, "send")
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(
        reconciler,
        "_parse_args",
        lambda: SimpleNamespace(
            once=True, interval=30.0, stale_after_seconds=720, result_ttl_seconds=300
        ),
    )
    monkeypatch.setattr(reconciler, "_main_async", fake_main_async)
    monkeypatch.setattr(reconciler, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)

    reconciler.main()

    assert configure_calls == [False]
    assert async_calls == [(True, 720)]


@pytest.mark.asyncio
async def test_decrement_active_counter_clamps_negative_values() -> None:
    class NegativeRedis(FakeRedis):
        async def decr(self, key: str) -> int:
            self.decrements.append(key)
            return -1

    redis = NegativeRedis()

    await reconciler._decrement_active_counter(redis, user_id="user-1")

    assert redis.decrements == ["active:user-1"]
    assert redis.set_calls == [("active:user-1", 0)]


@pytest.mark.asyncio
async def test_cache_reconciled_state_is_noop_without_redis() -> None:
    await reconciler._cache_reconciled_state(
        redis_client=None,
        state=ReconciledTaskState(
            task_id=uuid4(),
            user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
            status=TaskStatus.EXPIRED,
            billing_state=BillingState.EXPIRED,
            model_class=ModelClass.SMALL,
        ),
        result_ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_main_async_logs_resolution_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    stop_event = asyncio.Event()

    class FakePool:
        async def close(self) -> None:
            events.append(("pool_closed", {}))

    class RuntimeRedis(FakeRedis):
        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            events.append(("redis_closed", {}))

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

    class FakeTBClient:
        def close(self) -> None:
            events.append(("tb_closed", {}))

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_reconcile(*_: object, **__: object) -> int:
        stop_event.set()
        return 2

    monkeypatch.setattr(reconciler, "logger", FakeLogger())
    monkeypatch.setattr(
        reconciler,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.reconciler.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr(
        reconciler,
        "_build_billing",
        lambda _settings: (FakeTBClient(), FakeBilling(PendingTransferState.PENDING)),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(reconciler, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(reconciler, "reconcile_stale_reserved_tasks", fake_reconcile)

    await reconciler._main_async(
        once=False,
        stale_after_seconds=720,
        interval_seconds=0.1,
        result_ttl_seconds=300,
    )

    assert ("reconciler_stale_resolved", {"count": 2}) in events
    assert ("tb_closed", {}) in events
    assert ("redis_closed", {}) in events
    assert ("pool_closed", {}) in events


@pytest.mark.asyncio
async def test_main_async_waits_between_iterations_until_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wait_calls: list[float] = []
    stop_event = asyncio.Event()

    class FakePool:
        async def close(self) -> None:
            return None

    class RuntimeRedis(FakeRedis):
        async def ping(self) -> bool:
            return True

        async def close(self) -> None:
            return None

    class FakeTBClient:
        def close(self) -> None:
            return None

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_reconcile(*_: object, **__: object) -> int:
        return 0

    async def fake_wait_for(awaitable: Awaitable[bool], *, timeout: float) -> bool:
        wait_calls.append(timeout)
        stop_event.set()
        return await awaitable

    monkeypatch.setattr(
        reconciler,
        "load_settings",
        lambda: SimpleNamespace(
            postgres_dsn="postgresql://postgres:postgres@postgres:5432/postgres",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution3.workers.reconciler.Redis.from_url",
        lambda *_, **__: RuntimeRedis(),
    )
    monkeypatch.setattr(
        reconciler,
        "_build_billing",
        lambda _settings: (FakeTBClient(), FakeBilling(PendingTransferState.PENDING)),
    )
    monkeypatch.setattr("solution3.workers.reconciler.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(reconciler, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(reconciler, "reconcile_stale_reserved_tasks", fake_reconcile)
    monkeypatch.setattr("solution3.workers.reconciler.asyncio.wait_for", fake_wait_for)

    await reconciler._main_async(
        once=False,
        stale_after_seconds=720,
        interval_seconds=0.25,
        result_ttl_seconds=300,
    )

    assert wait_calls == [0.25]
