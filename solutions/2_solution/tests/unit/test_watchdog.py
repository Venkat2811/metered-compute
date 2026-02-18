from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from solution2.constants import ReservationState
from solution2.models.domain import CreditReservation
from solution2.workers import watchdog


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, states: dict[str, dict[str, str]] | None = None) -> None:
        self.closed = False
        self.states = states or {}
        self.deleted_keys: list[tuple[str, ...]] = []

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True

    async def hset(self, key: str, mapping: dict[str | bytes, str | int | float | bytes]) -> int:
        self.states[key] = {str(k): str(v) for k, v in mapping.items()}
        return 1

    async def expire(self, key: str, _ttl: int) -> bool:
        _ = key
        return True

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.states.get(key, {})

    async def delete(self, *keys: str) -> int:
        self.deleted_keys.append(tuple(keys))
        for key in keys:
            self.states.pop(key, None)
        return len(keys)

    async def scan_iter(self, match: str, count: int) -> Any:
        _ = (match, count)
        for key in list(self.states):
            if key.startswith("task:"):
                yield key


class _FakeGauge:
    def __init__(self) -> None:
        self.values: list[float] = []

    def set(self, value: float) -> None:
        self.values.append(value)


def _runtime(redis_client: _FakeRedis | None = None) -> watchdog.WatchdogRuntime:
    settings = SimpleNamespace(
        task_result_ttl_seconds=60,
        redis_task_state_ttl_seconds=120,
        watchdog_interval_seconds=30.0,
        watchdog_error_backoff_seconds=1.0,
        watchdog_scan_count=100,
        watchdog_metrics_port=9400,
    )
    return watchdog.WatchdogRuntime(
        settings=cast(Any, settings),
        db_pool=cast(Any, _FakePool()),
        redis_client=cast(Any, redis_client or _FakeRedis()),
    )


def _reservation(*, amount: int) -> CreditReservation:
    now = datetime.now(tz=UTC)
    return CreditReservation(
        reservation_id=uuid4(),
        task_id=uuid4(),
        user_id=uuid4(),
        amount=amount,
        state=ReservationState.RESERVED,
        expires_at=now - timedelta(seconds=10),
        created_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
    )


def test_task_id_from_task_key_parses_uuid() -> None:
    task_id = uuid4()
    parsed = watchdog._task_id_from_task_key(f"task:{task_id}")
    assert parsed == task_id
    assert watchdog._task_id_from_task_key("bad") is None


@pytest.mark.asyncio
async def test_process_expired_reservations_counts_only_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    first = _reservation(amount=15)
    second = _reservation(amount=5)
    cache_writes: list[str] = []

    async def fake_find_expired(*_: object, **__: object) -> list[CreditReservation]:
        return [first, second]

    async def fake_expire_reservation(
        *,
        runtime: watchdog.WatchdogRuntime,
        task_id: Any,
        user_id: Any,
        amount: int,
    ) -> tuple[bool, str | None]:
        _ = (runtime, task_id, user_id)
        if amount == 15:
            return True, "queue.batch"
        return False, None

    async def fake_write_timeout_cache(**kwargs: object) -> None:
        cache_writes.append(str(kwargs["task_id"]))

    monkeypatch.setattr(watchdog, "find_expired_reservations", fake_find_expired)
    monkeypatch.setattr(watchdog, "_expire_reservation", fake_expire_reservation)
    monkeypatch.setattr(watchdog, "_write_timeout_cache", fake_write_timeout_cache)

    expired_count, released_credits = await watchdog._process_expired_reservations(runtime)

    assert expired_count == 1
    assert released_credits == 15
    assert len(cache_writes) == 1


@pytest.mark.asyncio
async def test_refresh_reservation_metrics_sets_active_gauge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    gauge = _FakeGauge()

    async def fake_count_total_active_reservations(*_args: object, **_kwargs: object) -> int:
        return 7

    monkeypatch.setattr(
        watchdog,
        "count_total_active_reservations",
        fake_count_total_active_reservations,
    )
    monkeypatch.setattr(watchdog, "RESERVATIONS_ACTIVE_GAUGE", gauge)

    total = await watchdog._refresh_reservation_metrics(runtime)

    assert total == 7
    assert gauge.values == [7.0]


@pytest.mark.asyncio
async def test_cleanup_terminal_redis_deletes_expired_terminal_keys() -> None:
    now_epoch = int(datetime.now(tz=UTC).timestamp())
    old_epoch = now_epoch - 200
    recent_epoch = now_epoch - 10
    states: dict[str, dict[str, str]] = {}
    states[f"task:{uuid4()}"] = {"status": "COMPLETED", "completed_at_epoch": str(old_epoch)}
    states[f"task:{uuid4()}"] = {"status": "FAILED", "completed_at_epoch": str(recent_epoch)}
    states[f"task:{uuid4()}"] = {"status": "PENDING", "completed_at_epoch": str(old_epoch)}
    runtime = _runtime(redis_client=_FakeRedis(states=states))

    cleaned = await watchdog._cleanup_terminal_redis(runtime)
    fake_redis_client = cast(Any, runtime.redis_client)

    assert cleaned == 1
    assert len(fake_redis_client.deleted_keys) == 1


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

    fake_pool = _FakePool()
    fake_redis = _FakeRedis()
    calls = {"expired": 0, "cleanup": 0, "active": 0}

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_build_db_pool(*_: object, **__: object) -> _FakePool:
        return fake_pool

    async def fake_process_expired(*_: object, **__: object) -> tuple[int, int]:
        calls["expired"] += 1
        return 0, 0

    async def fake_cleanup(*_: object, **__: object) -> int:
        calls["cleanup"] += 1
        return 0

    async def fake_refresh(*_: object, **__: object) -> int:
        calls["active"] += 1
        return 0

    monkeypatch.setattr(watchdog, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(watchdog, "_build_db_pool", fake_build_db_pool)
    monkeypatch.setattr(
        "solution2.workers.watchdog.Redis.from_url",
        lambda *_args, **_kwargs: fake_redis,
    )
    monkeypatch.setattr(watchdog, "_process_expired_reservations", fake_process_expired)
    monkeypatch.setattr(watchdog, "_cleanup_terminal_redis", fake_cleanup)
    monkeypatch.setattr(watchdog, "_refresh_reservation_metrics", fake_refresh)
    monkeypatch.setattr("solution2.workers.watchdog.asyncio.Event", _OneShotEvent)
    monkeypatch.setattr("solution2.workers.watchdog.asyncio.get_running_loop", lambda: _FakeLoop())
    monkeypatch.setattr("solution2.workers.watchdog.start_http_server", lambda *_args: None)
    monkeypatch.setattr(
        watchdog,
        "load_settings",
        lambda: cast(
            Any,
            SimpleNamespace(
                app_name="mc-solution2",
                postgres_dsn="postgresql://postgres:postgres@localhost:5432/postgres",
                redis_url="redis://localhost:6379/0",
                db_pool_min_size=1,
                db_pool_max_size=2,
                db_pool_command_timeout_seconds=0.1,
                db_statement_timeout_batch_ms=1000,
                db_idle_in_transaction_timeout_ms=500,
                db_pool_max_inactive_connection_lifetime_seconds=60.0,
                redis_socket_timeout_seconds=0.1,
                redis_socket_connect_timeout_seconds=0.1,
                watchdog_interval_seconds=0.01,
                watchdog_error_backoff_seconds=0.01,
                watchdog_scan_count=100,
                watchdog_metrics_port=9400,
                task_result_ttl_seconds=60,
                redis_task_state_ttl_seconds=120,
            ),
        ),
    )

    await watchdog.main_async()

    assert calls["expired"] == 1
    assert calls["cleanup"] == 1
    assert calls["active"] == 1
    assert fake_pool.closed is True
    assert fake_redis.closed is True
