"""Unit tests for Restate workflow logic."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from solution5 import workflows
from solution5.workflows import _compute


class _DummyCtx:
    """Minimal Restate context used for deterministic unit tests."""

    def __init__(self, run_results: list[Any]) -> None:
        self._run_results = list(run_results)

    async def run(self, _name: str, fn):  # noqa: ANN001
        if not self._run_results:
            return await fn()
        return self._run_results.pop(0)


class TestCompute:
    def test_compute_basic(self) -> None:
        result = _compute(3, 4)
        assert result == {"sum": 7, "product": 12}

    def test_compute_zeros(self) -> None:
        result = _compute(0, 0)
        assert result == {"sum": 0, "product": 0}

    def test_compute_negative(self) -> None:
        result = _compute(-5, 3)
        assert result == {"sum": -2, "product": -15}

    def test_compute_large(self) -> None:
        result = _compute(1_000_000, 2_000_000)
        assert result == {"sum": 3_000_000, "product": 2_000_000_000_000}


class TestExecuteTaskLifecycle:
    @staticmethod
    def _set_state() -> tuple[MagicMock, MagicMock, MagicMock]:
        pool = MagicMock()
        billing = MagicMock()
        redis = MagicMock()
        workflows._state.clear()
        workflows._state.update({"pg_pool": pool, "billing": billing, "redis": redis})
        return pool, billing, redis

    @staticmethod
    async def _run_execute_task(request: dict[str, int | str], run_results: list[Any]) -> dict[str, Any]:
        ctx = _DummyCtx(run_results)
        return await workflows.execute_task(ctx=ctx, request=request)

    @pytest.mark.asyncio
    async def test_start_transition_rejects_when_not_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=True)
        update_status = AsyncMock(return_value=False)
        monkeypatch.setattr(workflows.repository, "update_task_status_if_match", update_status)
        monkeypatch.setattr(workflows.repository, "get_task_status", AsyncMock(return_value="COMPLETED"))

        response = await self._run_execute_task({"task_id": "t1", "tb_transfer_id": "1", "x": 1, "y": 1}, [])
        assert response["status"] == "COMPLETED"
        assert update_status.await_count == 1

    @pytest.mark.asyncio
    async def test_capture_failure_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=False)
        monkeypatch.setattr(
            workflows.repository,
            "update_task_status_if_match",
            AsyncMock(side_effect=[True, True]),
        )
        monkeypatch.setattr(workflows.repository, "get_task_status", AsyncMock(return_value="RUNNING"))

        response = await self._run_execute_task(
            {"task_id": "t2", "tb_transfer_id": "2", "x": 2, "y": 3},
            [{"sum": 5, "product": 6}, False],
        )
        assert response["status"] == "FAILED"
        assert workflows.repository.update_task_status_if_match.await_count == 2  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_completed_transition_writes_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, billing, _ = self._set_state()
        billing.capture_credits = MagicMock(return_value=True)
        monkeypatch.setattr(
            workflows.repository,
            "update_task_status_if_match",
            AsyncMock(side_effect=[True, True]),
        )
        monkeypatch.setattr(workflows.repository, "get_task_status", AsyncMock(return_value="COMPLETED"))
        workflow_cache_task = AsyncMock()
        monkeypatch.setattr(workflows.cache, "cache_task", workflow_cache_task)

        response = await self._run_execute_task(
            {"task_id": "t3", "tb_transfer_id": "3", "x": 4, "y": 5},
            [{"sum": 9, "product": 20}, True],
        )
        assert response["status"] == "COMPLETED"
        workflow_cache_task.assert_awaited_once()
