from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

import solution1.workers.reaper as reaper_module
from solution1.constants import TaskStatus
from solution1.core.defaults import DEFAULT_USER1_API_KEY
from solution1.models.domain import TaskRecord
from tests.constants import (
    TASK_ID_PRIMARY,
    TASK_ID_PRIMARY_STR,
    TEST_USER_ID,
    TEST_USER_ID_STR,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.deleted: list[str] = []

    async def scan_iter(self, match: str, count: int | None = None) -> object:
        _ = count
        if match == "pending:*":
            for key in list(self.hashes):
                if key.startswith("pending:"):
                    yield key

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        self.hashes.pop(key, None)
        return 1

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: int | str) -> bool:
        self.values[key] = str(value)
        return True

    async def srem(self, key: str, value: str) -> int:
        members = self.sets.get(key, set())
        existed = value in members
        members.discard(value)
        self.sets[key] = members
        return 1 if existed else 0


@pytest.mark.asyncio
async def test_process_pending_markers_refunds_orphan_and_cleans_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis()
    now = int(time.time())
    task_id = TASK_ID_PRIMARY_STR
    user_id = TEST_USER_ID_STR
    redis_client.hashes[f"pending:{task_id}"] = {
        "created_at_epoch": str(now - 100),
        "user_id": user_id,
        "task_id": task_id,
        "cost": "10",
        "idempotency_value": "idem-1",
        "api_key": "key",
    }

    async def fake_get_task(*_: object, **__: object) -> None:
        return None

    refund_calls: list[tuple[UUID, int]] = []

    async def fake_refund_and_decrement_active(**kwargs: object) -> str:
        refund_calls.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["amount"])))
        return "new-sha"

    monkeypatch.setattr(reaper_module, "get_task", fake_get_task)
    monkeypatch.setattr(
        reaper_module,
        "refund_and_decrement_active",
        fake_refund_and_decrement_active,
    )

    recovered, sha = await reaper_module._process_pending_markers(
        pool=object(),
        redis_client=redis_client,  # type: ignore[arg-type]
        decrement_script_sha="old-sha",
        orphan_timeout_seconds=60,
        scan_count=100,
        max_markers_per_cycle=500,
    )

    assert recovered == 1
    assert sha == "new-sha"
    assert refund_calls == [(UUID(user_id), 10)]
    assert f"pending:{task_id}" in redis_client.deleted
    assert f"idem:{user_id}:idem-1" in redis_client.deleted


@pytest.mark.asyncio
async def test_flush_credit_snapshots_only_persists_valid_credit_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis()
    redis_client.sets["credits:dirty"] = {f"credits:{TEST_USER_ID_STR}", "bad-key"}
    redis_client.values[f"credits:{TEST_USER_ID_STR}"] = "123"

    snapshot_calls: list[tuple[UUID, int]] = []

    async def fake_upsert_credit_snapshot(*_: object, **kwargs: object) -> None:
        snapshot_calls.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["balance"])))

    monkeypatch.setattr(reaper_module, "upsert_credit_snapshot", fake_upsert_credit_snapshot)

    flushed = await reaper_module._flush_credit_snapshots(
        pool=object(),
        redis_client=redis_client,  # type: ignore[arg-type]
    )

    assert flushed == 1
    assert snapshot_calls == [(TEST_USER_ID, 123)]
    assert redis_client.sets["credits:dirty"] == set()


@pytest.mark.asyncio
async def test_run_once_aggregates_cycle_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process_pending_markers(**_: object) -> tuple[int, str]:
        return 2, "mid-sha"

    async def fake_process_stuck_tasks(**_: object) -> tuple[int, str]:
        return 3, "end-sha"

    async def fake_flush_credit_snapshots(**_: object) -> int:
        return 4

    async def fake_bulk_expire_old_terminal_tasks(*_: object, **__: object) -> int:
        return 5

    async def fake_run_credit_drift_audit(**_: object) -> tuple[int, int]:
        return 6, 1

    async def fake_purge_old_credit_transactions(*_: object, **__: object) -> int:
        return 7

    async def fake_purge_old_credit_drift_audit(*_: object, **__: object) -> int:
        return 8

    monkeypatch.setattr(reaper_module, "_process_pending_markers", fake_process_pending_markers)
    monkeypatch.setattr(reaper_module, "_process_stuck_tasks", fake_process_stuck_tasks)
    monkeypatch.setattr(reaper_module, "_flush_credit_snapshots", fake_flush_credit_snapshots)
    monkeypatch.setattr(reaper_module, "_run_credit_drift_audit", fake_run_credit_drift_audit)
    monkeypatch.setattr(
        reaper_module,
        "bulk_expire_old_terminal_tasks",
        fake_bulk_expire_old_terminal_tasks,
    )
    monkeypatch.setattr(
        reaper_module,
        "purge_old_credit_transactions",
        fake_purge_old_credit_transactions,
    )
    monkeypatch.setattr(
        reaper_module,
        "purge_old_credit_drift_audit",
        fake_purge_old_credit_drift_audit,
    )
    monkeypatch.setattr(
        reaper_module,
        "load_settings",
        lambda: SimpleNamespace(
            orphan_marker_timeout_seconds=60,
            task_stuck_timeout_seconds=120,
            task_result_ttl_seconds=3600,
            reaper_retention_batch_size=500,
            reaper_credit_transaction_retention_seconds=86400,
            reaper_credit_drift_audit_retention_seconds=86400,
        ),
    )

    sha = await reaper_module._run_once(
        pool=object(),
        redis_client=cast(Any, object()),
        decrement_script_sha="start-sha",
    )

    assert sha == "end-sha"


@pytest.mark.asyncio
async def test_run_once_skips_retention_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_process_pending_markers(**_: object) -> tuple[int, str]:
        return 0, "start-sha"

    async def fake_process_stuck_tasks(**_: object) -> tuple[int, str]:
        return 0, "start-sha"

    async def fake_flush_credit_snapshots(**_: object) -> int:
        return 0

    async def fake_run_credit_drift_audit(**_: object) -> tuple[int, int]:
        return 0, 0

    async def fake_bulk_expire_old_terminal_tasks(*_: object, **__: object) -> int:
        return 0

    async def fake_purge_old_credit_transactions(*_: object, **__: object) -> int:
        raise AssertionError("credit transaction purge should not run with 0 retention window")

    async def fake_purge_old_credit_drift_audit(*_: object, **__: object) -> int:
        raise AssertionError("drift audit purge should not run with 0 retention window")

    monkeypatch.setattr(reaper_module, "_process_pending_markers", fake_process_pending_markers)
    monkeypatch.setattr(reaper_module, "_process_stuck_tasks", fake_process_stuck_tasks)
    monkeypatch.setattr(reaper_module, "_flush_credit_snapshots", fake_flush_credit_snapshots)
    monkeypatch.setattr(reaper_module, "_run_credit_drift_audit", fake_run_credit_drift_audit)
    monkeypatch.setattr(
        reaper_module,
        "bulk_expire_old_terminal_tasks",
        fake_bulk_expire_old_terminal_tasks,
    )
    monkeypatch.setattr(
        reaper_module,
        "purge_old_credit_transactions",
        fake_purge_old_credit_transactions,
    )
    monkeypatch.setattr(
        reaper_module,
        "purge_old_credit_drift_audit",
        fake_purge_old_credit_drift_audit,
    )
    monkeypatch.setattr(
        reaper_module,
        "load_settings",
        lambda: SimpleNamespace(
            orphan_marker_timeout_seconds=60,
            task_stuck_timeout_seconds=120,
            task_result_ttl_seconds=3600,
            reaper_retention_batch_size=500,
            reaper_credit_transaction_retention_seconds=0,
            reaper_credit_drift_audit_retention_seconds=0,
        ),
    )

    await reaper_module._run_once(
        pool=object(),
        redis_client=cast(Any, object()),
        decrement_script_sha="start-sha",
    )


@pytest.mark.asyncio
async def test_run_credit_drift_audit_reconciles_snapshot_and_logs_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis()
    user_with_drift = TEST_USER_ID
    user_missing = UUID("00000000-0000-0000-0000-000000000123")
    redis_client.values[f"credits:{user_with_drift}"] = "95"

    async def fake_list_credit_snapshots(*_: object) -> list[tuple[UUID, int]]:
        return [(user_with_drift, 100), (user_missing, 50)]

    snapshot_updates: list[tuple[UUID, int]] = []

    async def fake_upsert_credit_snapshot(*_: object, **kwargs: object) -> None:
        snapshot_updates.append((cast(UUID, kwargs["user_id"]), cast(int, kwargs["balance"])))

    audit_rows: list[tuple[UUID, int, int, int, str | None]] = []

    async def fake_insert_credit_drift_audit(*_: object, **kwargs: object) -> None:
        audit_rows.append(
            (
                cast(UUID, kwargs["user_id"]),
                cast(int, kwargs["redis_balance"]),
                cast(int, kwargs["db_balance"]),
                cast(int, kwargs["drift"]),
                cast(str | None, kwargs["action_taken"]),
            )
        )

    monkeypatch.setattr(reaper_module, "list_credit_snapshots", fake_list_credit_snapshots)
    monkeypatch.setattr(reaper_module, "upsert_credit_snapshot", fake_upsert_credit_snapshot)
    monkeypatch.setattr(reaper_module, "insert_credit_drift_audit", fake_insert_credit_drift_audit)

    audited, reconciled = await reaper_module._run_credit_drift_audit(
        pool=object(),
        redis_client=redis_client,  # type: ignore[arg-type]
    )

    assert audited == 2
    assert reconciled == 1
    assert snapshot_updates == [(user_with_drift, 95)]
    assert redis_client.values[f"credits:{user_missing}"] == "50"
    assert audit_rows == [
        (user_with_drift, 95, 100, -5, "snapshot_reconciled"),
        (user_missing, 50, 50, 0, "redis_hydrated_from_snapshot"),
    ]


@pytest.mark.asyncio
async def test_process_pending_markers_cleans_invalid_and_existing_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis()
    now = int(time.time())
    redis_client.hashes["pending:bad"] = {"created_at_epoch": str(now - 100)}
    redis_client.hashes["pending:existing"] = {
        "created_at_epoch": str(now - 100),
        "user_id": TEST_USER_ID_STR,
        "task_id": TASK_ID_PRIMARY_STR,
        "cost": "10",
        "idempotency_value": "idem-existing",
    }

    async def fake_get_task(*_: object, **__: object) -> TaskRecord:
        return TaskRecord(
            task_id=TASK_ID_PRIMARY,
            api_key="key",
            user_id=TEST_USER_ID,
            x=1,
            y=2,
            cost=10,
            status=TaskStatus.PENDING,
            result=None,
            error=None,
            runtime_ms=None,
            idempotency_key=None,
            created_at=datetime.now(tz=UTC),
            started_at=None,
            completed_at=None,
        )

    monkeypatch.setattr(reaper_module, "get_task", fake_get_task)

    recovered, sha = await reaper_module._process_pending_markers(
        pool=object(),
        redis_client=redis_client,  # type: ignore[arg-type]
        decrement_script_sha="unchanged",
        orphan_timeout_seconds=60,
        scan_count=100,
        max_markers_per_cycle=500,
    )

    assert recovered == 0
    assert sha == "unchanged"
    assert "pending:bad" in redis_client.deleted
    assert "pending:existing" in redis_client.deleted


@pytest.mark.asyncio
async def test_process_pending_markers_respects_max_per_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_client = _FakeRedis()
    now = int(time.time())
    for index in range(3):
        redis_client.hashes[f"pending:{index}"] = {
            "created_at_epoch": str(now - 100),
            "user_id": TEST_USER_ID_STR,
            "task_id": str(UUID(int=index + 1)),
            "cost": "1",
            "idempotency_value": f"idem-{index}",
        }

    async def fake_get_task(*_: object, **__: object) -> None:
        return None

    async def fake_refund_and_decrement_active(**_: object) -> str:
        return "sha"

    monkeypatch.setattr(reaper_module, "get_task", fake_get_task)
    monkeypatch.setattr(
        reaper_module,
        "refund_and_decrement_active",
        fake_refund_and_decrement_active,
    )

    recovered, _sha = await reaper_module._process_pending_markers(
        pool=object(),
        redis_client=redis_client,  # type: ignore[arg-type]
        decrement_script_sha="sha",
        orphan_timeout_seconds=60,
        scan_count=100,
        max_markers_per_cycle=2,
    )

    assert recovered == 2


@pytest.mark.asyncio
async def test_flush_credit_snapshots_handles_missing_balances() -> None:
    redis_client = _FakeRedis()
    redis_client.sets["credits:dirty"] = {f"credits:{TEST_USER_ID_STR}"}

    flushed = await reaper_module._flush_credit_snapshots(
        pool=object(),
        redis_client=redis_client,  # type: ignore[arg-type]
    )

    assert flushed == 0
    assert redis_client.sets["credits:dirty"] == set()


@pytest.mark.asyncio
async def test_main_async_runs_single_cycle_and_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeLoop:
        def add_signal_handler(self, *_: object) -> None:
            return None

        def remove_signal_handler(self, *_: object) -> None:
            return None

    class _FakeEvent:
        def __init__(self) -> None:
            self._set = False

        def is_set(self) -> bool:
            return self._set

        def set(self) -> None:
            self._set = True

        async def wait(self) -> None:
            self._set = True

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

    fake_pool = _FakePool()
    fake_redis = _FakeRedisMain()
    run_once_calls = {"count": 0}

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_create_pool(**_: object) -> _FakePool:
        return fake_pool

    async def fake_run_once(*_: object, **__: object) -> str:
        run_once_calls["count"] += 1
        return "sha"

    async def fake_wait_for(awaitable: object, timeout: float) -> object:
        _ = timeout
        return await cast(Any, awaitable)

    monkeypatch.setattr(reaper_module, "run_migrations", fake_run_migrations)
    monkeypatch.setattr("solution1.workers.reaper.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution1.workers.reaper.Redis.from_url", lambda *_args, **_kwargs: fake_redis
    )
    monkeypatch.setattr(reaper_module, "_run_once", fake_run_once)
    monkeypatch.setattr("solution1.workers.reaper.asyncio.Event", _FakeEvent)
    monkeypatch.setattr("solution1.workers.reaper.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr("solution1.workers.reaper.asyncio.get_running_loop", lambda: _FakeLoop())
    monkeypatch.setattr("solution1.workers.reaper.start_http_server", lambda _port: None)
    monkeypatch.setattr(
        reaper_module,
        "load_settings",
        lambda: SimpleNamespace(
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
            orphan_marker_timeout_seconds=60,
            task_stuck_timeout_seconds=300,
            task_result_ttl_seconds=3600,
            reaper_interval_seconds=0.1,
            reaper_error_backoff_seconds=0.05,
            reaper_metrics_port=9201,
        ),
    )

    await reaper_module.main_async()

    assert run_once_calls["count"] == 1
    assert fake_pool.closed is True
    assert fake_redis.closed is True


@pytest.mark.asyncio
async def test_main_async_survives_cycle_failure_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeLoop:
        def add_signal_handler(self, *_: object) -> None:
            return None

        def remove_signal_handler(self, *_: object) -> None:
            return None

    class _ControlledEvent:
        def __init__(self) -> None:
            self._set = False

        def is_set(self) -> bool:
            return self._set

        def set(self) -> None:
            self._set = True

        async def wait(self) -> None:
            return None

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

    fake_pool = _FakePool()
    fake_redis = _FakeRedisMain()
    fake_event = _ControlledEvent()

    run_once_calls = {"count": 0}

    async def fake_run_migrations(*_: object) -> list[str]:
        return []

    async def fake_create_pool(**_: object) -> _FakePool:
        return fake_pool

    async def fake_run_once(*_: object, **__: object) -> str:
        run_once_calls["count"] += 1
        if run_once_calls["count"] == 1:
            raise RuntimeError("transient reaper cycle failure")
        return "sha"

    wait_calls = {"count": 0}

    async def fake_wait_for(awaitable: object, timeout: float) -> object:
        _ = timeout
        wait_calls["count"] += 1
        if wait_calls["count"] == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise TimeoutError
        fake_event.set()
        return await cast(Any, awaitable)

    monkeypatch.setattr(reaper_module, "run_migrations", fake_run_migrations)
    monkeypatch.setattr("solution1.workers.reaper.asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(
        "solution1.workers.reaper.Redis.from_url", lambda *_args, **_kwargs: fake_redis
    )
    monkeypatch.setattr(reaper_module, "_run_once", fake_run_once)
    monkeypatch.setattr("solution1.workers.reaper.asyncio.Event", lambda: fake_event)
    monkeypatch.setattr("solution1.workers.reaper.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr("solution1.workers.reaper.asyncio.get_running_loop", lambda: _FakeLoop())
    monkeypatch.setattr("solution1.workers.reaper.start_http_server", lambda _port: None)
    monkeypatch.setattr(
        reaper_module,
        "load_settings",
        lambda: SimpleNamespace(
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
            orphan_marker_timeout_seconds=60,
            task_stuck_timeout_seconds=300,
            task_result_ttl_seconds=3600,
            reaper_interval_seconds=0.1,
            reaper_error_backoff_seconds=0.05,
            reaper_metrics_port=9201,
        ),
    )

    await reaper_module.main_async()

    assert run_once_calls["count"] == 2
    assert fake_pool.closed is True
    assert fake_redis.closed is True


def test_reaper_main_delegates_to_asyncio_run(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = {"called": False}

    def fake_run(coro: object) -> None:
        ran["called"] = True
        close = getattr(coro, "close", None)
        if callable(close):
            close()

    monkeypatch.setattr("asyncio.run", fake_run)
    reaper_module.main()
    assert ran["called"] is True


def test_construct_task_record_for_stuck_paths() -> None:
    task = TaskRecord(
        task_id=TASK_ID_PRIMARY,
        api_key=DEFAULT_USER1_API_KEY,
        user_id=TEST_USER_ID,
        x=1,
        y=2,
        cost=10,
        status=TaskStatus.RUNNING,
        result=None,
        error=None,
        runtime_ms=None,
        idempotency_key=None,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        completed_at=None,
    )
    assert task.status == TaskStatus.RUNNING
