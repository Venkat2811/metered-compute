"""Unit tests for Restate workflow logic."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
import restate

from solution5 import cache as cache_module
from solution5 import repository, workflows
from solution5.settings import Settings


class _DummyCtx:
    """Minimal Restate context used for deterministic unit tests."""

    def __init__(self, run_results: list[Any]) -> None:
        self._run_results = list(run_results)

    async def run(self, _name: str, fn: Callable[[], Any]) -> Any:  # noqa: ANN001
        if self._run_results:
            result = self._run_results.pop(0)
            if inspect.isawaitable(result):
                return await result
            return result

        result = fn()
        if inspect.isawaitable(result):
            return await result
        return result


class TestExecuteTaskLifecycle:
    @staticmethod
    def _set_state() -> tuple[MagicMock, MagicMock, MagicMock]:
        pool = MagicMock()
        billing = MagicMock()
        redis = AsyncMock()
        workflows._state.clear()
        workflows._state.update(
            {
                "pg_pool": pool,
                "billing": billing,
                "redis": redis,
                "settings": Settings(),
            },
        )
        return pool, billing, redis

    @staticmethod
    async def _run_execute_task(request: dict[str, int | str], run_results: list[Any]) -> dict[str, Any]:
        ctx = _DummyCtx(run_results)
        return await workflows.execute_task(ctx=cast(restate.Context, ctx), request=request)

    @pytest.mark.asyncio
    async def test_start_transition_rejects_when_not_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=True)
        update_status = AsyncMock(return_value=False)
        monkeypatch.setattr(repository, "update_task_status_if_match", update_status)
        monkeypatch.setattr(repository, "get_task_status", AsyncMock(return_value="COMPLETED"))

        response = await self._run_execute_task({"task_id": "t1", "tb_transfer_id": "1", "x": 1, "y": 1}, [])
        assert response["status"] == "COMPLETED"
        assert update_status.await_count == 1

    @pytest.mark.asyncio
    async def test_capture_failure_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=False)
        monkeypatch.setattr(
            repository,
            "update_task_status_if_match",
            AsyncMock(side_effect=[True, True]),
        )
        monkeypatch.setattr(repository, "get_task_status", AsyncMock(return_value="RUNNING"))
        monkeypatch.setattr(
            workflows,
            "request_compute_sync",
            lambda **_: {"sum": 5, "product": 6},
        )

        response = await self._run_execute_task(
            {"task_id": "t2", "tb_transfer_id": "2", "x": 2, "y": 3},
            [],
        )
        assert response["status"] == "FAILED"
        assert billing.release_credits.call_count == 1
        assert cast(AsyncMock, repository.update_task_status_if_match).await_count == 2

    @pytest.mark.asyncio
    async def test_completed_transition_writes_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=True)
        monkeypatch.setattr(
            workflows,
            "request_compute_sync",
            lambda **_: {"sum": 9, "product": 20},
        )
        monkeypatch.setattr(
            repository,
            "update_task_status_if_match",
            AsyncMock(side_effect=[True, True]),
        )
        monkeypatch.setattr(repository, "get_task_status", AsyncMock(return_value="COMPLETED"))
        workflow_cache_task = AsyncMock()
        monkeypatch.setattr(cache_module, "cache_task", workflow_cache_task)

        response = await self._run_execute_task(
            {"task_id": "t3", "tb_transfer_id": "3", "x": 4, "y": 5},
            [],
        )
        assert response["status"] == "COMPLETED"
        workflow_cache_task.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compute_failure_marks_task_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, redis = self._set_state()
        billing.capture_credits = MagicMock(return_value=False)
        monkeypatch.setattr(
            repository,
            "update_task_status_if_match",
            AsyncMock(side_effect=[True, True]),
        )
        cache_invalidate_task = AsyncMock()
        monkeypatch.setattr(cache_module, "invalidate_task", cache_invalidate_task)
        monkeypatch.setattr(
            workflows,
            "request_compute_sync",
            lambda **_: (_ for _ in ()).throw(RuntimeError("compute down")),
        )

        response = await self._run_execute_task(
            {"task_id": "t4", "tb_transfer_id": "4", "x": 4, "y": 5},
            [],
        )
        assert response["status"] == "FAILED"
        cache_invalidate_task.assert_awaited_once_with(redis, "t4")
        assert cast(AsyncMock, repository.update_task_status_if_match).await_count == 2

    @pytest.mark.asyncio
    async def test_compute_hands_request_to_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=True)
        worker_call = AsyncMock()
        worker_call.return_value = {"sum": 11, "product": 12}
        monkeypatch.setattr(workflows, "request_compute_sync", worker_call)
        monkeypatch.setattr(
            repository,
            "update_task_status_if_match",
            AsyncMock(side_effect=[True, True]),
        )
        monkeypatch.setattr(repository, "get_task_status", AsyncMock(return_value="COMPLETED"))

        response = await self._run_execute_task(
            {"task_id": "t5", "tb_transfer_id": "5", "x": 3, "y": 8},
            [],
        )
        assert response["status"] == "COMPLETED"
        worker_call.assert_called_once_with(
            task_id="t5",
            x=3,
            y=8,
            base_url=ANY,
            timeout_seconds=ANY,
            retry_attempts=ANY,
        )
