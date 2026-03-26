from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from contextlib import suppress
from types import SimpleNamespace

import pytest

from solution3 import constants, main
from solution3.workers import _bootstrap_worker


def test_main_module_exposes_app_instance() -> None:
    assert main.app is not None


def test_constants_match_bootstrap_contract() -> None:
    assert constants.DEFAULT_TASK_STATUS == constants.TaskStatus.PENDING
    assert constants.TASK_CANCELLABLE_STATUSES == (
        constants.TaskStatus.PENDING,
        constants.TaskStatus.RUNNING,
    )
    assert constants.TASK_TERMINAL_STATUSES == (
        constants.TaskStatus.COMPLETED,
        constants.TaskStatus.FAILED,
        constants.TaskStatus.CANCELLED,
    )
    assert constants.UserRole.ADMIN.value == "admin"
    assert constants.SubscriptionTier.ENTERPRISE.value == "enterprise"


def test_parse_interval_uses_cli_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "argparse.ArgumentParser.parse_args", lambda self: SimpleNamespace(interval=7.5)
    )
    assert _bootstrap_worker._parse_interval() == 7.5


def test_run_worker_configures_logging_and_runs_async_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[str, float]] = []

    async def fake_main_async(*, name: str, interval_seconds: float) -> None:
        async_calls.append((name, interval_seconds))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    def fake_asyncio_run(coro: object) -> None:
        assert asyncio.iscoroutine(coro)
        with suppress(StopIteration):
            coro.send(None)

    monkeypatch.setattr(_bootstrap_worker, "_parse_interval", lambda: 4.0)
    monkeypatch.setattr(_bootstrap_worker, "_main_async", fake_main_async)
    monkeypatch.setattr(_bootstrap_worker, "configure_logging", fake_configure_logging)
    monkeypatch.setattr("solution3.workers._bootstrap_worker.asyncio.run", fake_asyncio_run)

    _bootstrap_worker.run_worker("solution3_worker")

    assert configure_calls == [False]
    assert async_calls == [("solution3_worker", 4.0)]


@pytest.mark.asyncio
async def test_main_async_logs_heartbeat_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    signal_handlers: list[Callable[[], None]] = []

    class _FakeLogger:
        def info(self, event: str, **_: object) -> None:
            events.append(event)

    class _FakeLoop:
        def add_signal_handler(self, _sig: int, callback: object, *args: object) -> None:
            def _runner() -> None:
                assert callable(callback)
                callback(*args)

            signal_handlers.append(_runner)

    monkeypatch.setattr(_bootstrap_worker, "logger", _FakeLogger())
    monkeypatch.setattr(
        "solution3.workers._bootstrap_worker.asyncio.get_running_loop",
        lambda: _FakeLoop(),
    )

    task = asyncio.create_task(
        _bootstrap_worker._main_async(name="solution3_worker", interval_seconds=30.0)
    )
    await asyncio.sleep(0)
    assert signal_handlers
    signal_handlers[0]()
    await task

    assert events == [
        "bootstrap_worker_started",
        "bootstrap_worker_heartbeat",
        "bootstrap_worker_stopped",
    ]


@pytest.mark.parametrize(
    ("module_name", "expected_name"),
    [
        ("solution3.workers.worker", "solution3_worker"),
        ("solution3.workers.dispatcher", "solution3_dispatcher"),
        ("solution3.workers.outbox_relay", "solution3_outbox_relay"),
        ("solution3.workers.projector", "solution3_projector"),
        ("solution3.workers.reconciler", "solution3_reconciler"),
        ("solution3.workers.watchdog", "solution3_watchdog"),
        ("solution3.workers.webhook_dispatcher", "solution3_webhook_dispatcher"),
    ],
)
def test_worker_entrypoints_delegate_to_run_worker(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    expected_name: str,
) -> None:
    module = importlib.import_module(module_name)
    calls: list[str] = []
    monkeypatch.setattr(module, "run_worker", lambda name: calls.append(name))

    module.main()

    assert calls == [expected_name]
