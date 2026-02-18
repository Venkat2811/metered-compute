from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ResponseError

import solution1.workers.stream_worker as stream_worker
from solution1.constants import ModelClass, TaskStatus
from solution1.observability import metrics as metrics_module
from solution1.workers.stream_worker import StreamWorkerRuntime


@dataclass
class _FakeRedis:
    hashes: dict[str, dict[str, str]] = field(default_factory=dict)
    expiries: dict[str, int] = field(default_factory=dict)
    acked: list[str] = field(default_factory=list)

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        existing = self.hashes.setdefault(key, {})
        existing.update(mapping)
        return 1

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        self.expiries[key] = ttl_seconds
        return True

    async def xack(self, _stream: str, _group: str, message_id: str) -> int:
        self.acked.append(message_id)
        return 1

    async def exists(self, key: str) -> int:
        return 1 if key in self.hashes else 0

    async def delete(self, key: str) -> int:
        existed = key in self.hashes
        self.hashes.pop(key, None)
        self.expiries.pop(key, None)
        return 1 if existed else 0

    async def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        self.hashes[key] = {"value": value}
        self.expiries[key] = ttl_seconds
        return True


def _runtime(redis_client: _FakeRedis) -> StreamWorkerRuntime:
    settings = SimpleNamespace(
        app_name="mc-solution1-api",
        redis_tasks_stream_key="tasks:stream",
        redis_tasks_stream_group="workers",
        redis_task_state_ttl_seconds=60,
        task_result_ttl_seconds=60,
        stream_worker_read_count=1,
        stream_worker_block_ms=1000,
        stream_worker_claim_idle_ms=300000,
        stream_worker_claim_count=20,
        stream_worker_heartbeat_key="workers:stream:last_seen",
        stream_worker_heartbeat_ttl_seconds=30,
        stream_worker_error_backoff_seconds=1.0,
        orphan_marker_timeout_seconds=60,
    )
    return StreamWorkerRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, redis_client),
        decrement_script_sha="decr-sha",
        model=cast(Any, lambda x, y, _model: x + y),
        consumer_name="worker-test",
    )


class _FakeTxContext:
    async def __aenter__(self) -> _FakeTxContext:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConnection:
    def transaction(self) -> _FakeTxContext:
        return _FakeTxContext()


class _FakeAcquireContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self._connection = _FakeConnection()

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self._connection)


def test_parse_message_payload_valid() -> None:
    task_id = uuid4()
    user_id = uuid4()
    payload = json.dumps(
        {
            "x": 5,
            "y": 8,
            "user_id": str(user_id),
            "trace_id": "trace-1",
            "trace_context": {"traceparent": "00-abc-def-01"},
        }
    )
    parsed = stream_worker._parse_message_payload(
        {
            "task_id": str(task_id),
            "payload": payload,
            "user_id": str(user_id),
            "cost": "10",
        }
    )
    assert parsed is not None
    assert parsed.task_id == task_id
    assert parsed.user_id == user_id
    assert parsed.x == 5
    assert parsed.y == 8
    assert parsed.cost == 10
    assert parsed.model_class == ModelClass.SMALL
    assert parsed.trace_context == {"traceparent": "00-abc-def-01"}


def test_parse_message_payload_rejects_invalid_payload() -> None:
    parsed = stream_worker._parse_message_payload(
        {
            "task_id": str(uuid4()),
            "payload": "not-json",
            "user_id": str(uuid4()),
            "cost": "10",
        }
    )
    assert parsed is None


def test_parse_message_payload_reads_model_class_from_payload() -> None:
    task_id = uuid4()
    user_id = uuid4()
    parsed = stream_worker._parse_message_payload(
        {
            "task_id": str(task_id),
            "payload": (f'{{"x": 3, "y": 9, "user_id": "{user_id}", "model_class": "large"}}'),
            "user_id": str(user_id),
            "cost": "40",
        }
    )
    assert parsed is not None
    assert parsed.model_class == ModelClass.LARGE
    assert parsed.cost == 40


@pytest.mark.asyncio
async def test_worker_model_warmup_is_async_and_runtime_sleep_is_blocking_only_in_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warmup_calls: list[float] = []
    runtime_calls: list[float] = []

    async def fake_async_sleep(seconds: float) -> None:
        warmup_calls.append(seconds)

    def fake_sleep(seconds: float) -> None:
        runtime_calls.append(seconds)

    monkeypatch.setattr("solution1.workers.stream_worker.asyncio.sleep", fake_async_sleep)
    monkeypatch.setattr("solution1.workers.stream_worker.time.sleep", fake_sleep)

    model = stream_worker.WorkerModel()
    await model.warmup()
    result = model(1, 2, ModelClass.SMALL)

    assert warmup_calls == [10]
    assert runtime_calls == [2.0]
    assert result == 3


@pytest.mark.asyncio
async def test_process_message_success_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)

    async def _running(*_: object, **__: object) -> bool:
        return True

    async def _completed(*_: object, **__: object) -> bool:
        return True

    async def _decrement(**_: object) -> str:
        return "decr-new"

    monkeypatch.setattr(stream_worker, "update_task_running", _running)
    monkeypatch.setattr(stream_worker, "update_task_completed", _completed)
    monkeypatch.setattr(stream_worker, "decrement_active_counter", _decrement)

    result_sha = await stream_worker._process_message(
        runtime,
        message_id="1-0",
        fields={
            "task_id": str(task_id),
            "payload": f'{{"x": 4, "y": 6, "user_id": "{user_id}", "trace_id": "trace-42"}}',
            "user_id": str(user_id),
            "cost": "10",
        },
    )

    assert result_sha == "decr-new"
    assert runtime.decrement_script_sha == "decr-new"
    assert redis_client.acked == ["1-0"]
    assert redis_client.hashes[f"task:{task_id}"]["status"] == TaskStatus.COMPLETED.value
    assert redis_client.hashes[f"result:{task_id}"]["status"] == TaskStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_process_message_acks_invalid_stream_message() -> None:
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)

    result_sha = await stream_worker._process_message(
        runtime,
        message_id="bad-0",
        fields={"payload": "{}"},
    )

    assert result_sha == "decr-sha"
    assert redis_client.acked == ["bad-0"]


@pytest.mark.asyncio
async def test_process_message_skips_when_task_not_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)

    async def _running(*_: object, **__: object) -> bool:
        return False

    async def _get_task(*_: object, **__: object) -> Any:
        return SimpleNamespace(status=TaskStatus.COMPLETED)

    monkeypatch.setattr(stream_worker, "update_task_running", _running)
    monkeypatch.setattr(stream_worker, "get_task", _get_task)

    result_sha = await stream_worker._process_message(
        runtime,
        message_id="2-0",
        fields={
            "task_id": str(task_id),
            "payload": f'{{"x": 1, "y": 2, "user_id": "{user_id}"}}',
            "user_id": str(user_id),
            "cost": "10",
        },
    )

    assert result_sha == "decr-sha"
    assert redis_client.acked == ["2-0"]


@pytest.mark.asyncio
async def test_process_message_keeps_pending_when_task_row_missing_with_pending_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)

    async def _running(*_: object, **__: object) -> bool:
        return False

    async def _missing_task(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(stream_worker, "update_task_running", _running)
    monkeypatch.setattr(stream_worker, "get_task", _missing_task)
    redis_client.hashes[f"pending:{task_id}"] = {"task_id": str(task_id)}

    result_sha = await stream_worker._process_message(
        runtime,
        message_id="2-1",
        fields={
            "task_id": str(task_id),
            "payload": f'{{"x": 2, "y": 3, "user_id": "{user_id}"}}',
            "user_id": str(user_id),
            "cost": "10",
        },
    )

    assert result_sha == "decr-sha"
    assert redis_client.acked == []


@pytest.mark.asyncio
async def test_process_message_acks_orphan_missing_row_without_pending_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)
    redis_client.hashes[f"task:{task_id}"] = {"status": TaskStatus.PENDING.value}

    async def _running(*_: object, **__: object) -> bool:
        return False

    async def _missing_task(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(stream_worker, "update_task_running", _running)
    monkeypatch.setattr(stream_worker, "get_task", _missing_task)

    result_sha = await stream_worker._process_message(
        runtime,
        message_id="0-0",
        fields={
            "task_id": str(task_id),
            "payload": f'{{"x": 2, "y": 3, "user_id": "{user_id}"}}',
            "user_id": str(user_id),
            "cost": "10",
        },
    )

    assert result_sha == "decr-sha"
    assert redis_client.acked == ["0-0"]
    assert f"task:{task_id}" not in redis_client.hashes


@pytest.mark.asyncio
async def test_process_message_keeps_pending_when_marker_missing_within_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)
    redis_client.hashes[f"task:{task_id}"] = {"status": TaskStatus.PENDING.value}

    async def _running(*_: object, **__: object) -> bool:
        return False

    async def _missing_task(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(stream_worker, "update_task_running", _running)
    monkeypatch.setattr(stream_worker, "get_task", _missing_task)

    message_id = f"{int(time.time() * 1000)}-0"
    result_sha = await stream_worker._process_message(
        runtime,
        message_id=message_id,
        fields={
            "task_id": str(task_id),
            "payload": f'{{"x": 2, "y": 3, "user_id": "{user_id}"}}',
            "user_id": str(user_id),
            "cost": "10",
        },
    )

    assert result_sha == "decr-sha"
    assert redis_client.acked == []
    assert f"task:{task_id}" in redis_client.hashes


@pytest.mark.asyncio
async def test_handle_failure_refunds_on_task_and_ledger_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)
    runtime.db_pool = cast(Any, _FakePool())

    update_calls: list[UUID] = []
    insert_calls: list[tuple[UUID, int, str]] = []
    refund_calls: list[tuple[UUID, int]] = []
    cache_calls: list[str] = []
    state_calls: list[str] = []

    async def fake_update_task_failed(*_: object, **kwargs: object) -> bool:
        update_calls.append(cast(UUID, kwargs["task_id"]))
        return True

    async def fake_insert_credit_transaction(*_: object, **kwargs: object) -> None:
        insert_calls.append(
            (cast(UUID, kwargs["user_id"]), cast(int, kwargs["delta"]), str(kwargs["reason"]))
        )

    async def fake_refund_and_decrement_active(**kwargs: object) -> str:
        refund_calls.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "decr-new"

    async def fake_store_result_cache(*_: object, **__: object) -> None:
        cache_calls.append("stored")

    async def fake_update_task_state(*_: object, **kwargs: object) -> None:
        state = cast(TaskStatus, kwargs["status"])
        state_calls.append(state.value)

    monkeypatch.setattr(stream_worker, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(stream_worker, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        stream_worker, "refund_and_decrement_active", fake_refund_and_decrement_active
    )
    monkeypatch.setattr(stream_worker, "_store_result_cache", fake_store_result_cache)
    monkeypatch.setattr(stream_worker, "_update_task_state", fake_update_task_state)

    sha = await stream_worker._handle_failure(
        runtime=runtime,
        message=stream_worker.StreamMessage(
            message_id="99-0",
            task_id=task_id,
            user_id=user_id,
            cost=10,
            model_class=ModelClass.SMALL,
            x=1,
            y=2,
            trace_id="trace-42",
            trace_context={},
        ),
        error=RuntimeError("worker failure"),
    )

    assert sha == "decr-new"
    assert runtime.decrement_script_sha == "decr-new"
    assert update_calls == [task_id]
    assert insert_calls == [(user_id, 10, "failure_refund")]
    assert refund_calls == [(user_id, 10)]
    assert cache_calls == ["stored"]
    assert state_calls == [TaskStatus.FAILED.value]
    assert redis_client.acked == ["99-0"]


@pytest.mark.asyncio
async def test_handle_failure_no_refund_when_task_transition_not_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)
    runtime.db_pool = cast(Any, _FakePool())

    refund_calls: list[str] = []
    cache_calls: list[str] = []
    state_calls: list[str] = []

    async def fake_update_task_failed(*_args: object, **_kwargs: object) -> bool:
        return False

    async def fake_insert_credit_transaction(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("credit insert should not run")

    async def fake_refund_and_decrement_active(**_: object) -> str:
        refund_calls.append("refunded")
        return "decr-new"

    async def fake_store_result_cache(*_: object, **__: object) -> None:
        cache_calls.append("stored")

    async def fake_update_task_state(*_: object, **kwargs: object) -> None:
        state = cast(TaskStatus, kwargs["status"])
        state_calls.append(state.value)

    monkeypatch.setattr(stream_worker, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(stream_worker, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        stream_worker, "refund_and_decrement_active", fake_refund_and_decrement_active
    )
    monkeypatch.setattr(stream_worker, "_store_result_cache", fake_store_result_cache)
    monkeypatch.setattr(stream_worker, "_update_task_state", fake_update_task_state)

    sha = await stream_worker._handle_failure(
        runtime=runtime,
        message=stream_worker.StreamMessage(
            message_id="99-1",
            task_id=task_id,
            user_id=user_id,
            cost=10,
            model_class=ModelClass.SMALL,
            x=1,
            y=2,
            trace_id="trace-42",
            trace_context={},
        ),
        error=RuntimeError("worker conflict"),
    )

    assert sha == "decr-sha"
    assert runtime.decrement_script_sha == "decr-sha"
    assert refund_calls == []
    assert cache_calls == []
    assert state_calls == []
    assert redis_client.acked == ["99-1"]


@pytest.mark.asyncio
async def test_handle_failure_no_refund_when_db_transaction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    user_id = uuid4()
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)
    runtime.db_pool = cast(Any, _FakePool())

    refund_calls: list[str] = []

    async def fake_update_task_failed(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("db unavailable")

    async def fake_insert_credit_transaction(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("credit insert should not run")

    async def fake_refund_and_decrement_active(**_: object) -> str:
        refund_calls.append("refunded")
        return "decr-new"

    monkeypatch.setattr(stream_worker, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(stream_worker, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(
        stream_worker, "refund_and_decrement_active", fake_refund_and_decrement_active
    )

    sha = await stream_worker._handle_failure(
        runtime=runtime,
        message=stream_worker.StreamMessage(
            message_id="99-2",
            task_id=task_id,
            user_id=user_id,
            cost=10,
            model_class=ModelClass.SMALL,
            x=1,
            y=2,
            trace_id="trace-42",
            trace_context={},
        ),
        error=RuntimeError("worker tx broken"),
    )

    assert sha == "decr-sha"
    assert runtime.decrement_script_sha == "decr-sha"
    assert refund_calls == []
    assert redis_client.acked == ["99-2"]


@pytest.mark.asyncio
async def test_claim_idle_messages_parses_xautoclaim_payload() -> None:
    task_id = uuid4()
    user_id = uuid4()

    class _ClaimRedis(_FakeRedis):
        async def xautoclaim(self, **_: object) -> tuple[str, list[tuple[str, dict[str, str]]]]:
            return (
                "0-0",
                [
                    (
                        "10-0",
                        {
                            "task_id": str(task_id),
                            "payload": f'{{"x": 7, "y": 8, "user_id": "{user_id}"}}',
                            "user_id": str(user_id),
                            "cost": "10",
                        },
                    )
                ],
            )

    redis_client = _ClaimRedis()
    runtime = _runtime(redis_client)

    next_start, claimed = await stream_worker._claim_idle_messages(runtime, start_id="0-0")

    assert next_start == "0-0"
    assert claimed == [
        (
            "10-0",
            {
                "task_id": str(task_id),
                "payload": f'{{"x": 7, "y": 8, "user_id": "{user_id}"}}',
                "user_id": str(user_id),
                "cost": "10",
            },
        )
    ]


@pytest.mark.asyncio
async def test_claim_idle_messages_respects_min_idle_window() -> None:
    task_id = uuid4()
    user_id = uuid4()
    healthy_inflight_idle_ms = 7_000

    class _ClaimRedis(_FakeRedis):
        async def xautoclaim(
            self, **kwargs: object
        ) -> tuple[str, list[tuple[str, dict[str, str]]]]:
            min_idle_time = int(cast(int, kwargs["min_idle_time"]))
            if min_idle_time <= healthy_inflight_idle_ms:
                return (
                    "0-0",
                    [
                        (
                            "10-0",
                            {
                                "task_id": str(task_id),
                                "payload": f'{{"x": 1, "y": 2, "user_id": "{user_id}"}}',
                                "user_id": str(user_id),
                                "cost": "10",
                            },
                        )
                    ],
                )
            return ("0-0", [])

    redis_client = _ClaimRedis()
    runtime = _runtime(redis_client)
    runtime.settings.stream_worker_claim_idle_ms = 15_000
    _next_start, claimed = await stream_worker._claim_idle_messages(runtime, start_id="0-0")
    assert claimed == []

    runtime.settings.stream_worker_claim_idle_ms = 2_000
    _next_start, claimed = await stream_worker._claim_idle_messages(runtime, start_id="0-0")
    assert len(claimed) == 1


@pytest.mark.asyncio
async def test_read_new_messages_recreates_group_on_nogroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoGroupRedis(_FakeRedis):
        calls: int = 0

        async def xreadgroup(
            self, **_: object
        ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
            self.calls += 1
            raise ResponseError("NOGROUP No such key")

    redis_client = _NoGroupRedis()
    runtime = _runtime(redis_client)
    recreated = {"count": 0}

    async def _ensure_group(_: StreamWorkerRuntime) -> None:
        recreated["count"] += 1

    monkeypatch.setattr(stream_worker, "_ensure_consumer_group", _ensure_group)

    entries = await stream_worker._read_new_messages(runtime)

    assert entries == []
    assert recreated["count"] == 1


@pytest.mark.asyncio
async def test_set_worker_heartbeat_uses_key_and_ttl() -> None:
    redis_client = _FakeRedis()
    runtime = _runtime(redis_client)

    await stream_worker._set_worker_heartbeat(runtime)

    assert runtime.settings.stream_worker_heartbeat_key in redis_client.hashes
    assert (
        redis_client.expiries[runtime.settings.stream_worker_heartbeat_key]
        == runtime.settings.stream_worker_heartbeat_ttl_seconds
    )


@pytest.mark.asyncio
async def test_persist_stream_checkpoint_records_last_stream_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(_FakeRedis())
    checkpoint_calls: list[tuple[str, str]] = []

    async def _upsert_checkpoint(*_: object, **kwargs: object) -> None:
        checkpoint_calls.append(
            (
                cast(str, kwargs["consumer_group"]),
                cast(str, kwargs["last_stream_id"]),
            )
        )

    monkeypatch.setattr(stream_worker, "upsert_stream_checkpoint", _upsert_checkpoint)

    await stream_worker._persist_stream_checkpoint(runtime, "10-0")

    assert checkpoint_calls == [("workers", "10-0")]


@pytest.mark.asyncio
async def test_persist_stream_checkpoint_swallow_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(_FakeRedis())

    async def _upsert_checkpoint(*_: object, **__: object) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(stream_worker, "upsert_stream_checkpoint", _upsert_checkpoint)

    await stream_worker._persist_stream_checkpoint(runtime, "11-0")


@pytest.mark.asyncio
async def test_refresh_stream_group_metrics_sets_group_lag_and_pending() -> None:
    class _InfoRedis(_FakeRedis):
        async def xinfo_groups(self, _stream: str) -> list[dict[str, object]]:
            return [{"name": "workers", "pending": 7, "lag": 11}]

    runtime = _runtime(_InfoRedis())
    await stream_worker._refresh_stream_group_metrics(runtime)

    pending_value = metrics_module.STREAM_PENDING_ENTRIES.labels(group="workers")._value.get()
    lag_value = metrics_module.STREAM_CONSUMER_LAG.labels(group="workers")._value.get()
    assert pending_value == 7
    assert lag_value == 11


@pytest.mark.asyncio
async def test_refresh_stream_group_metrics_defaults_to_zero_when_group_missing() -> None:
    class _InfoRedis(_FakeRedis):
        async def xinfo_groups(self, _stream: str) -> list[dict[str, object]]:
            return [{"name": "other", "pending": 9, "lag": 12}]

    runtime = _runtime(_InfoRedis())
    await stream_worker._refresh_stream_group_metrics(runtime)

    pending_value = metrics_module.STREAM_PENDING_ENTRIES.labels(group="workers")._value.get()
    lag_value = metrics_module.STREAM_CONSUMER_LAG.labels(group="workers")._value.get()
    assert pending_value == 0
    assert lag_value == 0


def test_stream_message_age_seconds_handles_invalid_ids() -> None:
    assert stream_worker._stream_message_age_seconds("bad-id") is None
    assert stream_worker._stream_message_age_seconds("0-0") is not None


@pytest.mark.asyncio
async def test_main_async_runs_single_cycle_and_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    class _FakePool:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class _FakeRedisMain:
        def __init__(self) -> None:
            self.closed = False

        async def script_load(self, _: str) -> str:
            return "sha"

        async def close(self) -> None:
            self.closed = True

    class _FakeWorkerModel:
        def __init__(self) -> None:
            self.warmed = False

        async def warmup(self) -> None:
            self.warmed = True

        def __call__(self, x: int, y: int, model_class: ModelClass) -> int:
            _ = model_class
            return x + y

    fake_pool = _FakePool()
    fake_redis = _FakeRedisMain()
    heartbeat_calls = {"count": 0}

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_create_pool(**_: object) -> _FakePool:
        return fake_pool

    async def fake_ensure_group(*_: object, **__: object) -> None:
        return None

    async def fake_set_heartbeat(*_: object, **__: object) -> None:
        heartbeat_calls["count"] += 1

    async def fake_refresh_group_metrics(*_: object, **__: object) -> None:
        return None

    async def fake_claim_idle(
        *_: object, **__: object
    ) -> tuple[str, list[tuple[str, dict[str, str]]]]:
        return "0-0", []

    async def fake_read_new(*_: object, **__: object) -> list[tuple[str, dict[str, str]]]:
        return []

    monkeypatch.setattr(stream_worker, "run_migrations", fake_run_migrations)
    monkeypatch.setattr("solution1.workers.stream_worker.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution1.workers.stream_worker.Redis.from_url",
        lambda *_args, **_kwargs: fake_redis,
    )
    monkeypatch.setattr(stream_worker, "WorkerModel", _FakeWorkerModel)
    monkeypatch.setattr(stream_worker, "_ensure_consumer_group", fake_ensure_group)
    monkeypatch.setattr(stream_worker, "_set_worker_heartbeat", fake_set_heartbeat)
    monkeypatch.setattr(stream_worker, "_refresh_stream_group_metrics", fake_refresh_group_metrics)
    monkeypatch.setattr(stream_worker, "_claim_idle_messages", fake_claim_idle)
    monkeypatch.setattr(stream_worker, "_read_new_messages", fake_read_new)
    monkeypatch.setattr("solution1.workers.stream_worker.asyncio.Event", _OneShotEvent)
    monkeypatch.setattr(
        "solution1.workers.stream_worker.asyncio.get_running_loop",
        lambda: _FakeLoop(),
    )
    monkeypatch.setattr("solution1.workers.stream_worker.start_http_server", lambda _port: None)
    monkeypatch.setattr(
        stream_worker,
        "load_settings",
        lambda: SimpleNamespace(
            app_name="mc-solution1-api",
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
            redis_url="redis://localhost:6379/0",
            db_pool_min_size=1,
            db_pool_max_size=2,
            db_pool_command_timeout_seconds=1.0,
            db_pool_max_inactive_connection_lifetime_seconds=1.0,
            db_statement_timeout_ms=50,
            db_idle_in_transaction_timeout_ms=500,
            redis_socket_timeout_seconds=0.05,
            redis_socket_connect_timeout_seconds=0.05,
            redis_tasks_stream_key="tasks:stream",
            redis_tasks_stream_group="workers",
            stream_worker_read_count=1,
            stream_worker_block_ms=1000,
            stream_worker_claim_idle_ms=15000,
            stream_worker_claim_count=5,
            stream_worker_heartbeat_key="workers:stream:last_seen",
            stream_worker_heartbeat_ttl_seconds=30,
            stream_worker_error_backoff_seconds=0.05,
            worker_metrics_port=9100,
        ),
    )

    await stream_worker.main_async()

    assert heartbeat_calls["count"] == 1
    assert fake_pool.closed is True
    assert fake_redis.closed is True
