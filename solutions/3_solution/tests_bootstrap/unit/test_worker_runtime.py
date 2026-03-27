from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import asyncpg
import pytest
from uuid6 import uuid7

from solution3.constants import BillingState, ModelClass, RequestMode, SubscriptionTier, TaskStatus
from solution3.models.domain import TaskCommand
from solution3.workers import worker


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}
        self.decrements: list[str] = []

    async def decr(self, key: str) -> int:
        self.decrements.append(key)
        return 0

    async def hset(self, key: str, mapping: Mapping[str, str]) -> None:
        self.hashes[key] = {**self.hashes.get(key, {}), **dict(mapping)}

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


class FakeBilling:
    def __init__(self, *, post_ok: bool = True, void_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.void_ok = void_ok
        self.post_calls: list[UUID | str] = []
        self.void_calls: list[UUID | str] = []

    def post_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        self.post_calls.append(pending_transfer_id)
        return self.post_ok

    def void_pending_transfer(self, *, pending_transfer_id: UUID | str) -> bool:
        self.void_calls.append(pending_transfer_id)
        return self.void_ok


class FakeModelRuntime:
    def __init__(
        self, *, result: dict[str, int] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result or {"sum": 5}
        self.error = error
        self.calls: list[TaskCommand] = []

    async def execute(self, task: TaskCommand) -> dict[str, int]:
        self.calls.append(task)
        if self.error is not None:
            raise self.error
        return self.result


class FakeQueueChannel:
    def __init__(self) -> None:
        self.ack_calls: list[int] = []
        self.nack_calls: list[tuple[int, bool]] = []
        self.queue_declarations: list[dict[str, object]] = []
        self.queue_bindings: list[dict[str, object]] = []
        self.queue_unbindings: list[dict[str, object]] = []
        self.consume_calls: list[dict[str, object]] = []
        self.cancel_calls: list[str] = []

    def basic_ack(self, *, delivery_tag: int) -> None:
        self.ack_calls.append(delivery_tag)

    def basic_nack(self, *, delivery_tag: int, requeue: bool) -> None:
        self.nack_calls.append((delivery_tag, requeue))

    def queue_declare(self, *, queue: str, durable: bool) -> None:
        self.queue_declarations.append({"queue": queue, "durable": durable})

    def queue_bind(self, *, queue: str, exchange: str, arguments: dict[str, str]) -> None:
        self.queue_bindings.append({"queue": queue, "exchange": exchange, "arguments": arguments})

    def queue_unbind(self, *, queue: str, exchange: str, arguments: dict[str, str]) -> None:
        self.queue_unbindings.append({"queue": queue, "exchange": exchange, "arguments": arguments})

    def basic_consume(self, *, queue: str, on_message_callback: object) -> str:
        self.consume_calls.append({"queue": queue, "callback": on_message_callback})
        return f"ctag-{queue}"

    def basic_cancel(self, consumer_tag: str) -> None:
        self.cancel_calls.append(consumer_tag)


def _task_command() -> TaskCommand:
    now = datetime.now(tz=UTC)
    return TaskCommand(
        task_id=uuid7(),
        user_id=UUID("47b47338-5355-4edc-860b-846d71a2a75a"),
        tier=SubscriptionTier.PRO,
        mode=RequestMode.ASYNC,
        model_class=ModelClass.SMALL,
        status=TaskStatus.PENDING,
        billing_state=BillingState.RESERVED,
        x=2,
        y=3,
        cost=10,
        tb_pending_transfer_id=uuid7(),
        callback_url=None,
        idempotency_key="idem-1",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_mark_task_running_updates_db_and_hot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task_command()
    redis = FakeRedis()
    db_calls: list[UUID] = []

    async def fake_update_task_running(_pool: object, *, task_id: UUID) -> bool:
        db_calls.append(task_id)
        return True

    monkeypatch.setattr(worker, "update_task_running", fake_update_task_running)

    updated = await worker.mark_task_running(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        task=task,
        result_ttl_seconds=300,
    )

    assert updated is True
    assert db_calls == [task.task_id]
    assert redis.hashes[f"task:{task.task_id}"]["status"] == "RUNNING"
    assert redis.expirations[f"task:{task.task_id}"] == 300


@pytest.mark.asyncio
async def test_mark_task_running_skips_cache_when_db_guard_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()

    async def fake_update_task_running(_pool: object, *, task_id: UUID) -> bool:
        _ = task_id
        return False

    monkeypatch.setattr(worker, "update_task_running", fake_update_task_running)

    updated = await worker.mark_task_running(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        task=task,
        result_ttl_seconds=300,
    )

    assert updated is False
    assert redis.hashes == {}


@pytest.mark.asyncio
async def test_handle_successful_completion_posts_tb_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()
    billing = FakeBilling()
    finalized_calls: list[dict[str, object]] = []

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        finalized_calls.append(kwargs)
        return True

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=600,
        result={"sum": 5},
    )

    assert completed is True
    assert billing.post_calls == [task.tb_pending_transfer_id]
    assert billing.void_calls == []
    assert finalized_calls[0]["status"] == TaskStatus.COMPLETED
    assert finalized_calls[0]["billing_state"] == BillingState.CAPTURED
    assert redis.decrements == [f"active:{task.user_id}"]
    assert redis.hashes[f"task:{task.task_id}"]["status"] == "COMPLETED"
    assert redis.hashes[f"task:{task.task_id}"]["result"] == '{"sum": 5}'


@pytest.mark.asyncio
async def test_handle_failed_completion_voids_tb_and_updates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()
    billing = FakeBilling()
    finalized_calls: list[dict[str, object]] = []

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        finalized_calls.append(kwargs)
        return True

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        billing=billing,
        task=task,
        success=False,
        result_ttl_seconds=600,
        error="boom",
    )

    assert completed is True
    assert billing.post_calls == []
    assert billing.void_calls == [task.tb_pending_transfer_id]
    assert finalized_calls[0]["status"] == TaskStatus.FAILED
    assert finalized_calls[0]["billing_state"] == BillingState.RELEASED
    assert redis.hashes[f"task:{task.task_id}"]["status"] == "FAILED"
    assert redis.hashes[f"task:{task.task_id}"]["error"] == "boom"


@pytest.mark.asyncio
async def test_handle_completion_stops_when_tb_operation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    billing = FakeBilling(post_ok=False)
    finalized = False

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        nonlocal finalized
        _ = kwargs
        finalized = True
        return True

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, FakeRedis()),
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=600,
        result={"sum": 5},
    )

    assert completed is False
    assert finalized is False


@pytest.mark.asyncio
async def test_handle_completion_stops_when_terminal_update_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    redis = FakeRedis()
    billing = FakeBilling()

    async def fake_finalize(_pool: object, **kwargs: object) -> bool:
        _ = kwargs
        return False

    monkeypatch.setattr(worker, "finalize_task_command", fake_finalize)

    completed = await worker.handle_task_completion(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, redis),
        billing=billing,
        task=task,
        success=True,
        result_ttl_seconds=600,
        result={"sum": 5},
    )

    assert completed is False
    assert redis.hashes == {}


def test_task_command_from_event_maps_required_worker_fields() -> None:
    task = worker.task_command_from_event(
        {
            "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
            "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
            "tier": "pro",
            "mode": "async",
            "model_class": "small",
            "x": 2,
            "y": 3,
            "cost": 10,
            "tb_pending_transfer_id": "019c6db7-1439-7ace-bd2b-e1a3bb03328c",
        }
    )

    assert task.task_id == UUID("019c6db7-0857-7858-af93-f724ae4fe2c2")
    assert task.user_id == UUID("47b47338-5355-4edc-860b-846d71a2a75a")
    assert task.model_class == ModelClass.SMALL
    assert task.billing_state == BillingState.RESERVED


@pytest.mark.asyncio
async def test_process_task_event_marks_runs_and_completes_successfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _task_command()
    runtime = FakeModelRuntime(result={"sum": 9})
    events: list[tuple[str, object]] = []

    async def fake_mark(*_args: object, **_kwargs: object) -> bool:
        events.append(("mark", task.task_id))
        return True

    async def fake_complete(*_args: object, **kwargs: object) -> bool:
        events.append(("complete", kwargs["result"]))
        return True

    monkeypatch.setattr(worker, "mark_task_running", fake_mark)
    monkeypatch.setattr(worker, "handle_task_completion", fake_complete)

    processed = await worker.process_task_event(
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, FakeRedis()),
        billing=cast(worker.WorkerBilling, FakeBilling()),
        runtime=cast(worker.WorkerModelRuntime, runtime),
        task=task,
        result_ttl_seconds=300,
    )

    assert processed is True
    assert runtime.calls == [task]
    assert events == [("mark", task.task_id), ("complete", {"sum": 9})]


def test_handle_delivery_acks_success(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FakeQueueChannel()
    method = SimpleNamespace(delivery_tag=7)

    async def fake_process(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(worker, "process_task_event", fake_process)

    worker.handle_delivery(
        channel=cast(worker.WorkerQueueChannel, channel),
        method=method,
        body=json.dumps(
            {
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
                "tier": "pro",
                "mode": "async",
                "model_class": "small",
                "x": 2,
                "y": 3,
                "cost": 10,
                "tb_pending_transfer_id": "019c6db7-1439-7ace-bd2b-e1a3bb03328c",
            }
        ).encode("utf-8"),
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, FakeRedis()),
        billing=cast(worker.WorkerBilling, FakeBilling()),
        runtime=worker.WorkerModelRuntime(worker_id="worker-a"),
        result_ttl_seconds=300,
    )

    assert channel.ack_calls == [7]
    assert channel.nack_calls == []


def test_handle_delivery_nacks_requeue_on_processing_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeQueueChannel()
    method = SimpleNamespace(delivery_tag=9)

    async def fake_process(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(worker, "process_task_event", fake_process)

    worker.handle_delivery(
        channel=cast(worker.WorkerQueueChannel, channel),
        method=method,
        body=json.dumps(
            {
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "user_id": "47b47338-5355-4edc-860b-846d71a2a75a",
                "tier": "pro",
                "mode": "async",
                "model_class": "small",
                "x": 2,
                "y": 3,
                "cost": 10,
                "tb_pending_transfer_id": "019c6db7-1439-7ace-bd2b-e1a3bb03328c",
            }
        ).encode("utf-8"),
        db_pool=cast(asyncpg.Pool, object()),
        redis_client=cast(worker.WorkerRedis, FakeRedis()),
        billing=cast(worker.WorkerBilling, FakeBilling()),
        runtime=worker.WorkerModelRuntime(worker_id="worker-a"),
        result_ttl_seconds=300,
    )

    assert channel.ack_calls == []
    assert channel.nack_calls == [(9, True)]


def test_hot_queue_router_declares_binds_and_consumes_model_queue() -> None:
    channel = FakeQueueChannel()
    router = worker.WorkerHotRouteManager(
        channel=cast(worker.WorkerQueueChannel, channel),
        on_message_callback=object(),
    )

    router.activate(ModelClass.MEDIUM)

    assert channel.queue_declarations == [{"queue": "hot-medium", "durable": True}]
    assert channel.queue_bindings == [
        {
            "queue": "hot-medium",
            "exchange": "preloaded",
            "arguments": {"x-match": "all", "model_class": "medium"},
        }
    ]
    assert channel.consume_calls[0]["queue"] == "hot-medium"


def test_hot_queue_router_cancels_and_optionally_unbinds_hot_queue() -> None:
    channel = FakeQueueChannel()
    router = worker.WorkerHotRouteManager(
        channel=cast(worker.WorkerQueueChannel, channel),
        on_message_callback=object(),
    )
    router.activate(ModelClass.SMALL)

    router.deactivate(ModelClass.SMALL, unbind=True)

    assert channel.cancel_calls == ["ctag-hot-small"]
    assert channel.queue_unbindings == [
        {
            "queue": "hot-small",
            "exchange": "preloaded",
            "arguments": {"x-match": "all", "model_class": "small"},
        }
    ]


def test_main_configures_logging_and_runs_worker_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    loop_calls: list[tuple[float, int]] = []

    def fake_main_loop(*, interval_seconds: float, result_ttl_seconds: int) -> None:
        loop_calls.append((interval_seconds, result_ttl_seconds))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    monkeypatch.setattr(
        worker,
        "_parse_args",
        lambda: SimpleNamespace(interval=2.5, result_ttl_seconds=300),
    )
    monkeypatch.setattr(worker, "_main_loop", fake_main_loop)
    monkeypatch.setattr(worker, "configure_logging", fake_configure_logging)

    worker.main()

    assert configure_calls == [False]
    assert loop_calls == [(2.5, 300)]
