from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from solution2.constants import TaskCompletionMetricStatus, TaskStatus
from solution2.workers import worker


class _NullSpan:
    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _FakeModel:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def execute(self, command: worker.TaskExecutionCommand) -> tuple[dict[str, int], int]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return {"z": command.x + command.y}, 42

    async def warmup(self) -> None:
        return None


class _FakeRedis:
    def __init__(self) -> None:
        self.closed = False
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def ping(self) -> bool:
        return True

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.set_calls.append((key, value, ex))
        return True

    async def close(self) -> None:
        self.closed = True


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeCounter:
    def __init__(self) -> None:
        self.calls = 0

    def inc(self, amount: float = 1.0) -> None:
        self.calls += int(amount)


class _FakeGauge:
    def __init__(self) -> None:
        self.dec_calls = 0

    def dec(self, amount: float = 1.0) -> None:
        self.dec_calls += int(amount)


class _FakeGaugeWithLabels:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def labels(self, *, queue: str) -> _FakeGaugeWithLabels:
        self._current_queue = queue
        return self

    def set(self, value: int) -> None:
        queue = getattr(self, "_current_queue", "")
        self.values[queue] = value


def _delivery(*, event_type: str = "task.submitted") -> worker.RabbitMQDelivery:
    task_id = uuid4()
    event_payload = {
        "event_id": str(uuid4()),
        "task_id": str(task_id),
        "user_id": str(uuid4()),
        "x": 5,
        "y": 7,
        "cost": 10,
        "mode": "async",
        "tier": "free",
        "model_class": "small",
        "event_type": event_type,
    }
    return worker.RabbitMQDelivery(
        queue_name="queue.batch",
        routing_key="tasks.batch.free.small",
        delivery_tag=11,
        message_id=None,
        body=json.dumps(event_payload),
    )


def _runtime(*, model: _FakeModel | None = None) -> worker.WorkerRuntime:
    settings = SimpleNamespace(
        task_result_ttl_seconds=86_400,
        redis_task_state_ttl_seconds=86_400,
        worker_heartbeat_key="workers:worker:last_seen",
        worker_heartbeat_ttl_seconds=30,
    )
    fake_consumer = SimpleNamespace()
    return worker.WorkerRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, _FakePool()),
        redis_client=cast(Any, _FakeRedis()),
        consumer=cast(Any, fake_consumer),
        model=cast(Any, model or _FakeModel()),
    )


@pytest.mark.asyncio
async def test_parse_task_command_rejects_invalid_json() -> None:
    delivery = worker.RabbitMQDelivery(
        queue_name="queue.batch",
        routing_key="tasks.batch.free.small",
        delivery_tag=1,
        message_id=None,
        body="{bad",
    )

    parsed = worker._parse_task_command(delivery)

    assert parsed is None


@pytest.mark.asyncio
async def test_process_delivery_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(model=_FakeModel())
    delivery = _delivery(event_type="task.submitted")
    persisted: dict[str, object] = {}
    cache_updates: list[TaskStatus] = []
    captured_total = _FakeCounter()
    reservations_active = _FakeGauge()

    async def fake_set_running_state(**_: object) -> bool:
        return True

    async def fake_persist_success(
        *,
        runtime: worker.WorkerRuntime,
        command: worker.TaskExecutionCommand,
        result_payload: dict[str, int],
        runtime_ms: int,
    ) -> bool:
        _ = runtime
        persisted["task_id"] = command.task_id
        persisted["result"] = result_payload
        persisted["runtime_ms"] = runtime_ms
        return True

    async def fake_write_terminal_cache(**kwargs: object) -> None:
        cache_updates.append(cast(TaskStatus, kwargs["status"]))

    monkeypatch.setattr(worker, "start_span", lambda **_: _NullSpan())
    monkeypatch.setattr(worker, "_set_running_state", fake_set_running_state)
    monkeypatch.setattr(worker, "_persist_success", fake_persist_success)
    monkeypatch.setattr(worker, "_write_terminal_cache", fake_write_terminal_cache)
    monkeypatch.setattr(worker, "RESERVATIONS_CAPTURED_TOTAL", captured_total)
    monkeypatch.setattr(worker, "RESERVATIONS_ACTIVE_GAUGE", reservations_active)

    status = await worker._process_delivery(runtime=runtime, delivery=delivery)

    assert status == TaskCompletionMetricStatus.COMPLETED
    assert "task_id" in persisted
    assert persisted["result"] == {"z": 12}
    assert cache_updates == [TaskStatus.COMPLETED]
    assert captured_total.calls == 1
    assert reservations_active.dec_calls == 1


@pytest.mark.asyncio
async def test_process_delivery_failure_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(model=_FakeModel(fail=True))
    delivery = _delivery(event_type="task.submitted")
    failures: list[str] = []
    cache_updates: list[TaskStatus] = []
    released_total = _FakeCounter()
    reservations_active = _FakeGauge()

    async def fake_set_running_state(**_: object) -> bool:
        return True

    async def fake_persist_failure(
        *,
        runtime: worker.WorkerRuntime,
        command: worker.TaskExecutionCommand,
        error_message: str,
    ) -> bool:
        _ = runtime
        _ = command
        failures.append(error_message)
        return True

    async def fake_write_terminal_cache(**kwargs: object) -> None:
        cache_updates.append(cast(TaskStatus, kwargs["status"]))

    monkeypatch.setattr(worker, "start_span", lambda **_: _NullSpan())
    monkeypatch.setattr(worker, "_set_running_state", fake_set_running_state)
    monkeypatch.setattr(worker, "_persist_failure", fake_persist_failure)
    monkeypatch.setattr(worker, "_write_terminal_cache", fake_write_terminal_cache)
    monkeypatch.setattr(worker, "RESERVATIONS_RELEASED_TOTAL", released_total)
    monkeypatch.setattr(worker, "RESERVATIONS_ACTIVE_GAUGE", reservations_active)

    status = await worker._process_delivery(runtime=runtime, delivery=delivery)

    assert status == TaskCompletionMetricStatus.FAILED
    assert failures and failures[0].startswith("worker_execution_failed:")
    assert cache_updates == [TaskStatus.FAILED]
    assert released_total.calls == 1
    assert reservations_active.dec_calls == 1


@pytest.mark.asyncio
async def test_refresh_queue_depth_metrics_updates_gauge(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime(model=_FakeModel())
    queue_depth_gauge = _FakeGaugeWithLabels()

    class _DepthConsumer:
        def queue_depths(self, *, queue_names: tuple[str, ...]) -> dict[str, int]:
            assert queue_names == worker.WORKER_QUEUES
            return {
                "queue.realtime": 2,
                "queue.fast": 5,
                "queue.batch": 1,
            }

    runtime.consumer = cast(Any, _DepthConsumer())
    monkeypatch.setattr(worker, "RABBITMQ_QUEUE_DEPTH", queue_depth_gauge)

    await worker._refresh_queue_depth_metrics(runtime)

    assert queue_depth_gauge.values == {
        "queue.realtime": 2,
        "queue.fast": 5,
        "queue.batch": 1,
    }


@pytest.mark.asyncio
async def test_process_delivery_skips_non_submitted_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(model=_FakeModel())
    delivery = _delivery(event_type="task.completed")
    monkeypatch.setattr(worker, "start_span", lambda **_: _NullSpan())

    status = await worker._process_delivery(runtime=runtime, delivery=delivery)

    assert status == TaskCompletionMetricStatus.SKIPPED


@pytest.mark.asyncio
async def test_parse_non_submitted_event_without_xy_fields() -> None:
    payload = {
        "event_id": str(uuid4()),
        "task_id": str(uuid4()),
        "user_id": str(uuid4()),
        "mode": "async",
        "tier": "pro",
        "model_class": "small",
        "event_type": "task.completed",
    }
    delivery = worker.RabbitMQDelivery(
        queue_name="queue.fast",
        routing_key="tasks.async.pro.small",
        delivery_tag=12,
        message_id=None,
        body=json.dumps(payload),
    )

    parsed = worker._parse_task_command(delivery)

    assert parsed is not None
    assert parsed.event_type == "task.completed"
    assert parsed.x == 0
    assert parsed.y == 0


@pytest.mark.asyncio
async def test_main_async_single_cycle_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeLoop:
        def add_signal_handler(self, *_: object) -> None:
            return None

        def remove_signal_handler(self, *_: object) -> None:
            return None

    class _OneShotEvent:
        def __init__(self) -> None:
            self._checks = 0

        def is_set(self) -> bool:
            self._checks += 1
            return self._checks > 1

        def set(self) -> None:
            self._checks = 2

    class _FakeConsumer:
        def __init__(self) -> None:
            self.connected = False
            self.queues_declared = False
            self.closed = False
            self.acks = 0

        def connect(self) -> None:
            self.connected = True

        def ensure_queues(self, *, queue_names: tuple[str, ...]) -> None:
            _ = queue_names
            self.queues_declared = True

        def get_one(self, *, queue_names: tuple[str, ...]) -> worker.RabbitMQDelivery | None:
            _ = queue_names
            return None

        def ack(self, *, delivery_tag: int) -> None:
            _ = delivery_tag
            self.acks += 1

        def nack(self, *, delivery_tag: int, requeue: bool) -> None:
            _ = (delivery_tag, requeue)

        def close(self) -> None:
            self.closed = True

    fake_pool = _FakePool()
    fake_redis = _FakeRedis()
    fake_consumer = _FakeConsumer()

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_build_db_pool(*_: object, **__: object) -> _FakePool:
        return fake_pool

    monkeypatch.setattr(worker, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(worker, "_build_db_pool", fake_build_db_pool)
    monkeypatch.setattr(
        "solution2.workers.worker.Redis.from_url",
        lambda *_args, **_kwargs: fake_redis,
    )
    monkeypatch.setattr(worker, "RabbitMQTaskConsumer", lambda **_: fake_consumer)
    monkeypatch.setattr(worker, "WorkerModel", lambda: _FakeModel())
    monkeypatch.setattr(worker, "start_http_server", lambda *_: None)
    monkeypatch.setattr("solution2.workers.worker.asyncio.Event", _OneShotEvent)
    monkeypatch.setattr("solution2.workers.worker.asyncio.get_running_loop", lambda: _FakeLoop())
    monkeypatch.setattr(
        worker,
        "load_settings",
        lambda: cast(
            Any,
            SimpleNamespace(
                app_name="mc-solution2",
                postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
                redis_url="redis://localhost:6379/0",
                rabbitmq_url="amqp://guest:guest@rabbitmq:5672/",
                db_pool_min_size=1,
                db_pool_max_size=2,
                db_pool_command_timeout_seconds=0.1,
                db_statement_timeout_batch_ms=1000,
                db_idle_in_transaction_timeout_ms=500,
                db_pool_max_inactive_connection_lifetime_seconds=60.0,
                redis_socket_timeout_seconds=0.1,
                redis_socket_connect_timeout_seconds=0.1,
                worker_metrics_port=9100,
                worker_loop_bootstrap_timeout_seconds=5.0,
                worker_loop_task_timeout_seconds=5.0,
                worker_error_backoff_seconds=0.05,
                worker_heartbeat_key="workers:worker:last_seen",
                worker_heartbeat_ttl_seconds=30,
            ),
        ),
    )

    await worker.main_async()

    assert fake_consumer.connected is True
    assert fake_consumer.queues_declared is True
    assert fake_consumer.closed is True
    assert fake_pool.closed is True
    assert fake_redis.closed is True
