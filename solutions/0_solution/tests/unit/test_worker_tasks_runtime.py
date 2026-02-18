from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from redis.exceptions import NoScriptError

os.environ.setdefault("POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/postgres")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")

import solution0.workers.worker_tasks as worker_module
from solution0.workers.worker_tasks import WorkerRuntime, _decrement_active_sync, run_task


class _RetryCalled(Exception):
    pass


def _fake_loop_thread() -> threading.Thread:
    return threading.Thread(target=lambda: None, name="test-worker-loop")


@dataclass
class _FakeRedisSync:
    hashes: dict[str, dict[str, str]]
    values: dict[str, int]
    raise_noscript_once: bool = False
    evalsha_calls: int = 0

    def evalsha(self, *_: object) -> int:
        self.evalsha_calls += 1
        if self.raise_noscript_once and self.evalsha_calls == 1:
            raise NoScriptError("missing script")
        return 0

    def script_load(self, _: str) -> str:
        return "new-sha"

    def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.hashes[key] = mapping
        return 1

    def expire(self, key: str, _: int) -> bool:
        return key in self.hashes

    def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    def sadd(self, key: str, value: str) -> int:
        bucket = self.hashes.setdefault(key, {})
        bucket[value] = "1"
        return 1

    def close(self) -> None:
        return None

    @property
    def connection_pool(self) -> SimpleNamespace:
        return SimpleNamespace(disconnect=lambda: None)


@pytest.mark.asyncio
async def test_decrement_active_sync_recovers_when_script_cache_is_cold() -> None:
    redis_client = _FakeRedisSync(hashes={}, values={}, raise_noscript_once=True)
    sha = _decrement_active_sync(cast(Any, redis_client), script_sha="old-sha", user_id=uuid4())
    assert sha == "new-sha"


def test_run_task_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.new_event_loop()
    redis_client = _FakeRedisSync(hashes={}, values={})
    runtime = WorkerRuntime(
        event_loop=loop,
        loop_thread=_fake_loop_thread(),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, redis_client),
        decrement_script_sha="decr-sha",
        model=cast(Any, lambda x, y: x + y),
    )

    update_calls: list[str] = []

    async def fake_update_task_running(*_: object, **__: object) -> bool:
        update_calls.append("running")
        return True

    async def fake_update_task_completed(*_: object, **__: object) -> bool:
        update_calls.append("completed")
        return True

    monkeypatch.setattr(worker_module, "_runtime_or_raise", lambda: runtime)
    monkeypatch.setattr(
        worker_module,
        "_settings",
        lambda: SimpleNamespace(
            task_result_ttl_seconds=60,
            worker_db_timeout_seconds=5.0,
            worker_loop_task_timeout_seconds=30.0,
        ),
    )
    monkeypatch.setattr(worker_module, "update_task_running", fake_update_task_running)
    monkeypatch.setattr(worker_module, "update_task_completed", fake_update_task_completed)
    monkeypatch.setattr(worker_module, "_decrement_active_sync", lambda *_, **__: "decr-new")
    monkeypatch.setattr(
        worker_module,
        "_run_coroutine_on_worker_loop",
        lambda _loop, coroutine, *, timeout_seconds: loop.run_until_complete(coroutine),
    )

    task_id = str(uuid4())
    user_id = str(uuid4())
    result = run_task.run(task_id, 4, 5, 10, user_id, "api-key")

    assert result == {"z": 9}
    assert update_calls == ["running", "completed"]
    assert runtime.decrement_script_sha == "decr-new"
    assert redis_client.hashes[f"result:{task_id}"]["status"] == "COMPLETED"

    loop.close()


def test_run_task_retries_before_terminal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.new_event_loop()
    redis_client = _FakeRedisSync(hashes={}, values={})
    runtime = WorkerRuntime(
        event_loop=loop,
        loop_thread=_fake_loop_thread(),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, redis_client),
        decrement_script_sha="decr-sha",
        model=cast(Any, lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))),
    )

    async def fake_update_task_running(*_: object, **__: object) -> bool:
        return True

    monkeypatch.setattr(worker_module, "_runtime_or_raise", lambda: runtime)
    monkeypatch.setattr(
        worker_module,
        "_settings",
        lambda: SimpleNamespace(
            task_result_ttl_seconds=60,
            worker_db_timeout_seconds=5.0,
            worker_loop_task_timeout_seconds=30.0,
        ),
    )
    monkeypatch.setattr(worker_module, "update_task_running", fake_update_task_running)
    monkeypatch.setattr(
        worker_module,
        "_run_coroutine_on_worker_loop",
        lambda _loop, coroutine, *, timeout_seconds: loop.run_until_complete(coroutine),
    )
    monkeypatch.setattr(run_task, "max_retries", 3, raising=False)
    monkeypatch.setattr(
        run_task, "retry", lambda exc: (_ for _ in ()).throw(_RetryCalled(str(exc)))
    )

    with pytest.raises(_RetryCalled):
        run_task.run(str(uuid4()), 1, 2, 10, str(uuid4()), "key")

    loop.close()


def test_run_task_terminal_failure_refunds_and_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.new_event_loop()
    redis_client = _FakeRedisSync(hashes={}, values={})
    runtime = WorkerRuntime(
        event_loop=loop,
        loop_thread=_fake_loop_thread(),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, redis_client),
        decrement_script_sha="decr-sha",
        model=cast(Any, lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))),
    )

    failed_calls: list[str] = []
    credit_calls: list[str] = []

    async def fake_update_task_running(*_: object, **__: object) -> bool:
        return True

    async def fake_update_task_failed(*_: object, **__: object) -> bool:
        failed_calls.append("failed")
        return True

    async def fake_insert_credit_transaction(*_: object, **__: object) -> None:
        credit_calls.append("credit")

    class _FakeTxContext:
        async def __aenter__(self) -> _FakeTxContext:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    class _FakeConn:
        def transaction(self) -> _FakeTxContext:
            return _FakeTxContext()

    class _FakeAcquire:
        async def __aenter__(self) -> _FakeConn:
            return _FakeConn()

        async def __aexit__(self, *_: object) -> None:
            return None

    cast(Any, runtime).db_pool = SimpleNamespace(acquire=lambda: _FakeAcquire())

    monkeypatch.setattr(worker_module, "_runtime_or_raise", lambda: runtime)
    monkeypatch.setattr(
        worker_module,
        "_settings",
        lambda: SimpleNamespace(
            task_result_ttl_seconds=60,
            worker_db_timeout_seconds=5.0,
            worker_loop_task_timeout_seconds=30.0,
        ),
    )
    monkeypatch.setattr(worker_module, "update_task_running", fake_update_task_running)
    monkeypatch.setattr(worker_module, "update_task_failed", fake_update_task_failed)
    monkeypatch.setattr(worker_module, "insert_credit_transaction", fake_insert_credit_transaction)
    monkeypatch.setattr(worker_module, "_decrement_active_sync", lambda *_, **__: "decr-updated")
    monkeypatch.setattr(
        worker_module,
        "_run_coroutine_on_worker_loop",
        lambda _loop, coroutine, *, timeout_seconds: loop.run_until_complete(coroutine),
    )
    monkeypatch.setattr(run_task, "max_retries", 0, raising=False)

    task_id = str(uuid4())
    user_id = str(uuid4())
    result = run_task.run(task_id, 1, 2, 11, user_id, "key")

    assert result == {"z": 0}
    assert failed_calls == ["failed"]
    assert credit_calls == ["credit"]
    assert redis_client.values[f"credits:{user_id}"] == 11
    assert redis_client.hashes[f"result:{task_id}"]["status"] == "FAILED"

    loop.close()


def test_ensure_runtime_bootstraps_once(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.new_event_loop()
    runtime = WorkerRuntime(
        event_loop=loop,
        loop_thread=_fake_loop_thread(),
        db_pool=cast(Any, SimpleNamespace(close=lambda: None)),
        redis_client=cast(Any, _FakeRedisSync(hashes={}, values={})),
        decrement_script_sha="sha",
        model=cast(Any, lambda x, y: x + y),
    )
    bootstrap_calls = {"count": 0}
    metrics_calls = {"count": 0}

    monkeypatch.setattr(worker_module, "_runtime", None)
    monkeypatch.setattr(worker_module, "_metrics_server_started", False)

    def fake_bootstrap_runtime(_: object) -> WorkerRuntime:
        bootstrap_calls["count"] += 1
        return runtime

    monkeypatch.setattr(worker_module, "_bootstrap_runtime", fake_bootstrap_runtime)
    monkeypatch.setattr(
        worker_module, "_settings", lambda: SimpleNamespace(worker_metrics_port=9100)
    )
    monkeypatch.setattr(
        worker_module,
        "start_http_server",
        lambda *_: metrics_calls.__setitem__("count", metrics_calls["count"] + 1),
    )

    first = worker_module._ensure_runtime()
    second = worker_module._ensure_runtime()

    assert first is second
    assert bootstrap_calls["count"] == 1
    assert metrics_calls["count"] == 1

    loop.close()


def test_worker_model_init_and_call_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[int] = []
    monkeypatch.setattr(
        "solution0.workers.worker_tasks.time.sleep", lambda seconds: sleep_calls.append(seconds)
    )

    worker = worker_module.WorkerModel()
    result = worker(3, 4)

    assert result == 7
    assert sleep_calls == [10, 2]


def test_bootstrap_runtime_runs_migrations_and_loads_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migrations_called = {"count": 0}

    async def fake_run_migrations(_: str) -> list[str]:
        migrations_called["count"] += 1
        return []

    class _FakePool:
        async def close(self) -> None:
            return None

    async def fake_db_pool() -> _FakePool:
        return _FakePool()

    class _FakeRedis:
        def script_load(self, _: str) -> str:
            return "loaded-sha"

    class _FakeModel:
        def __call__(self, x: int, y: int) -> int:
            return x + y

    monkeypatch.setattr(worker_module, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(worker_module, "_db_pool", fake_db_pool)
    monkeypatch.setattr(
        "solution0.workers.worker_tasks.Redis.from_url", lambda *_args, **_kwargs: _FakeRedis()
    )
    monkeypatch.setattr(worker_module, "WorkerModel", _FakeModel)

    settings = SimpleNamespace(
        postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
        redis_url="redis://localhost:6379/0",
        db_idle_in_transaction_timeout_ms=500,
        db_statement_timeout_ms=50,
        redis_socket_timeout_seconds=0.05,
        redis_socket_connect_timeout_seconds=0.05,
        worker_loop_bootstrap_timeout_seconds=3.0,
        worker_loop_shutdown_timeout_seconds=3.0,
    )
    runtime = worker_module._bootstrap_runtime(cast(Any, settings))

    assert migrations_called["count"] == 1
    assert runtime.decrement_script_sha == "loaded-sha"
    worker_module._stop_event_loop(
        runtime.event_loop,
        loop_thread=runtime.loop_thread,
        timeout_seconds=1.0,
    )


def test_shutdown_worker_closes_loop_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.new_event_loop()
    closed = {"pool": False, "redis": False, "disconnect": False}

    class _FakePool:
        async def close(self) -> None:
            closed["pool"] = True

    class _FakeRedis:
        def close(self) -> None:
            closed["redis"] = True

        @property
        def connection_pool(self) -> SimpleNamespace:
            return SimpleNamespace(
                disconnect=lambda: closed.__setitem__("disconnect", True),
            )

    runtime = WorkerRuntime(
        event_loop=loop,
        loop_thread=_fake_loop_thread(),
        db_pool=cast(Any, _FakePool()),
        redis_client=cast(Any, _FakeRedis()),
        decrement_script_sha="sha",
        model=cast(Any, lambda x, y: x + y),
    )

    monkeypatch.setattr(worker_module, "_runtime", runtime)
    monkeypatch.setattr(worker_module, "_metrics_server_started", True)
    monkeypatch.setattr(
        worker_module,
        "_run_coroutine_on_worker_loop",
        lambda _loop, coroutine, *, timeout_seconds: loop.run_until_complete(coroutine),
    )
    monkeypatch.setattr(
        worker_module,
        "_stop_event_loop",
        lambda *_args, **_kwargs: loop.close(),
    )
    monkeypatch.setattr(
        worker_module,
        "_settings",
        lambda: SimpleNamespace(worker_loop_shutdown_timeout_seconds=1.0),
    )

    worker_module._shutdown_worker()

    assert closed == {"pool": True, "redis": True, "disconnect": True}
    assert worker_module._runtime is None
    assert worker_module._metrics_server_started is False


def test_initialize_worker_signal_handler_invokes_runtime_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}
    monkeypatch.setattr(worker_module, "_ensure_runtime", lambda: calls.__setitem__("count", 1))
    worker_module._initialize_worker()
    assert calls["count"] == 1
