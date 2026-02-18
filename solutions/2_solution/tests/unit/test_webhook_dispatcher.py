from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from solution2.constants import TaskStatus
from solution2.services import webhooks
from solution2.workers import webhook_dispatcher


class _FakeRedis:
    def __init__(self) -> None:
        self.pending: list[str] = []
        self.dlq: list[str] = []
        self.scheduled: dict[str, float] = {}

    async def rpush(self, key: str, value: str) -> int:
        if key.endswith(":queue"):
            self.pending.append(value)
        elif key.endswith(":dlq"):
            self.dlq.append(value)
        return 1

    async def lpush(self, key: str, value: str) -> int:
        if key.endswith(":queue"):
            self.pending.insert(0, value)
        elif key.endswith(":dlq"):
            self.dlq.insert(0, value)
        return 1

    async def blpop(self, key: str, timeout: int) -> tuple[str, str] | None:
        _ = timeout
        if key.endswith(":queue") and self.pending:
            value = self.pending.pop(0)
            return key, value
        return None

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        _ = key
        self.scheduled.update(mapping)
        return len(mapping)

    async def zrangebyscore(
        self,
        key: str,
        min: float | str,
        max: float | str,
        start: int = 0,
        num: int = 100,
    ) -> list[str]:
        _ = key
        _ = min
        upper_bound = float(max)
        due = [value for value, score in self.scheduled.items() if score <= upper_bound]
        return due[start : start + num]

    async def zrem(self, key: str, value: str) -> int:
        _ = key
        existed = value in self.scheduled
        self.scheduled.pop(value, None)
        return 1 if existed else 0

    async def llen(self, key: str) -> int:
        if key.endswith(":queue"):
            return len(self.pending)
        if key.endswith(":dlq"):
            return len(self.dlq)
        return 0

    async def zcard(self, key: str) -> int:
        _ = key
        return len(self.scheduled)


class _FakeHttpClient:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> Any:
        self.calls.append((url, json))
        return SimpleNamespace(status_code=self.status_code, text="")


def _runtime(
    redis_client: _FakeRedis, http_client: _FakeHttpClient
) -> webhook_dispatcher.DispatcherRuntime:
    settings = SimpleNamespace(
        webhook_queue_key="webhook:queue",
        webhook_scheduled_key="webhook:scheduled",
        webhook_dlq_key="webhook:dlq",
        webhook_dispatch_batch_size=50,
        webhook_max_attempts=3,
        webhook_initial_backoff_seconds=1.0,
        webhook_backoff_multiplier=2.0,
        webhook_max_backoff_seconds=30.0,
    )
    return webhook_dispatcher.DispatcherRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, redis_client),
        http_client=cast(Any, http_client),
    )


@pytest.mark.asyncio
async def test_process_raw_event_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = _FakeRedis()
    http_client = _FakeHttpClient(status_code=200)
    runtime = _runtime(redis_client, http_client)
    user_id = uuid4()
    task_id = uuid4()

    event = webhooks.build_terminal_webhook_event(
        user_id=user_id,
        task_id=task_id,
        status=TaskStatus.COMPLETED.value,
        result={"z": 9},
        error=None,
    )

    async def fake_get_webhook_subscription(*_: object, **__: object) -> Any:
        return SimpleNamespace(
            user_id=user_id,
            callback_url="https://example.com/webhook",
            enabled=True,
        )

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_webhook_subscription",
        fake_get_webhook_subscription,
    )

    await webhook_dispatcher._process_raw_event(
        runtime=runtime,
        raw_event=webhooks.serialize_webhook_event(event),
    )

    assert len(http_client.calls) == 1
    assert redis_client.scheduled == {}
    assert redis_client.dlq == []


@pytest.mark.asyncio
async def test_process_raw_event_schedules_retry_on_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis()
    http_client = _FakeHttpClient(status_code=503)
    runtime = _runtime(redis_client, http_client)
    user_id = uuid4()
    task_id = uuid4()

    event = webhooks.build_terminal_webhook_event(
        user_id=user_id,
        task_id=task_id,
        status=TaskStatus.FAILED.value,
        result=None,
        error="boom",
    )

    async def fake_get_webhook_subscription(*_: object, **__: object) -> Any:
        return SimpleNamespace(
            user_id=user_id,
            callback_url="https://example.com/webhook",
            enabled=True,
        )

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_webhook_subscription",
        fake_get_webhook_subscription,
    )

    await webhook_dispatcher._process_raw_event(
        runtime=runtime,
        raw_event=webhooks.serialize_webhook_event(event),
    )

    assert len(redis_client.scheduled) == 1
    retry_payload = next(iter(redis_client.scheduled))
    parsed = webhooks.parse_webhook_event(retry_payload)
    assert parsed is not None
    assert parsed.attempt == 1
    assert redis_client.dlq == []


@pytest.mark.asyncio
async def test_promote_scheduled_events_moves_due_entries_to_pending_queue() -> None:
    redis_client = _FakeRedis()
    http_client = _FakeHttpClient(status_code=200)
    runtime = _runtime(redis_client, http_client)
    now_ms = int(time.time() * 1000)
    redis_client.scheduled["event-1"] = now_ms - 10
    redis_client.scheduled["event-2"] = now_ms + 60_000

    promoted = await webhook_dispatcher._promote_scheduled_events(runtime)

    assert promoted == 1
    assert redis_client.pending == ["event-1"]
    assert "event-2" in redis_client.scheduled


@pytest.mark.asyncio
async def test_pop_pending_event_treats_redis_timeout_as_empty_poll() -> None:
    class _TimeoutRedis:
        async def blpop(self, key: str, timeout: int) -> tuple[str, str] | None:
            _ = (key, timeout)
            raise RedisTimeoutError("Timeout reading from redis:6379")

    runtime = webhook_dispatcher.DispatcherRuntime(
        settings=cast(
            Any,
            SimpleNamespace(
                webhook_queue_key="webhook:queue",
                webhook_dispatcher_poll_timeout_seconds=2,
            ),
        ),
        db_pool=cast(Any, object()),
        redis_client=cast(Any, _TimeoutRedis()),
        http_client=cast(Any, _FakeHttpClient(status_code=200)),
    )

    popped = await webhook_dispatcher._pop_pending_event(runtime)

    assert popped is None


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

        async def close(self) -> None:
            self.closed = True

    class _FakeHttpClientMain:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_pool = _FakePool()
    fake_redis = _FakeRedisMain()
    fake_http = _FakeHttpClientMain()
    cycle_calls = {"count": 0}

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_create_pool(**_: object) -> _FakePool:
        return fake_pool

    async def fake_promote(*_: object, **__: object) -> int:
        cycle_calls["count"] += 1
        return 0

    async def fake_pop(*_: object, **__: object) -> str | None:
        return None

    async def fake_refresh(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(webhook_dispatcher, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(
        "solution2.workers.webhook_dispatcher.asyncpg.create_pool",
        fake_create_pool,
    )
    monkeypatch.setattr(
        "solution2.workers.webhook_dispatcher.Redis.from_url",
        lambda *_args, **_kwargs: fake_redis,
    )
    monkeypatch.setattr(
        "solution2.workers.webhook_dispatcher.httpx.AsyncClient",
        lambda **_kwargs: fake_http,
    )
    monkeypatch.setattr(webhook_dispatcher, "_promote_scheduled_events", fake_promote)
    monkeypatch.setattr(webhook_dispatcher, "_pop_pending_event", fake_pop)
    monkeypatch.setattr(webhook_dispatcher, "_refresh_depth_metrics", fake_refresh)
    monkeypatch.setattr("solution2.workers.webhook_dispatcher.asyncio.Event", _OneShotEvent)
    monkeypatch.setattr(
        "solution2.workers.webhook_dispatcher.asyncio.get_running_loop",
        lambda: _FakeLoop(),
    )
    monkeypatch.setattr(
        "solution2.workers.webhook_dispatcher.start_http_server",
        lambda _port: None,
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "load_settings",
        lambda: SimpleNamespace(
            app_name="mc-solution2-api",
            webhook_enabled=True,
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
            redis_url="redis://localhost:6379/0",
            db_pool_min_size=1,
            db_pool_max_size=2,
            db_pool_command_timeout_seconds=1.0,
            db_statement_timeout_ms=50,
            db_idle_in_transaction_timeout_ms=500,
            db_pool_max_inactive_connection_lifetime_seconds=1.0,
            redis_socket_timeout_seconds=1.0,
            redis_socket_connect_timeout_seconds=1.0,
            webhook_metrics_port=9300,
            webhook_dispatch_error_backoff_seconds=0.05,
            webhook_dispatcher_poll_timeout_seconds=1,
            webhook_delivery_timeout_seconds=3.0,
            webhook_dispatch_batch_size=10,
            webhook_queue_key="webhook:queue",
            webhook_scheduled_key="webhook:scheduled",
            webhook_dlq_key="webhook:dlq",
            webhook_max_attempts=3,
            webhook_initial_backoff_seconds=1.0,
            webhook_backoff_multiplier=2.0,
            webhook_max_backoff_seconds=30.0,
        ),
    )

    await webhook_dispatcher.main_async()

    assert cycle_calls["count"] == 1
    assert fake_pool.closed is True
    assert fake_redis.closed is True
    assert fake_http.closed is True
