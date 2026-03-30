"""Unit tests for submission handoff safety paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from solution4 import app as app_module
from solution4 import cache, repository


@pytest.mark.asyncio
async def test_restate_handoff_failure_transitions_pending_to_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MagicMock()
    redis = AsyncMock()
    billing = MagicMock()
    billing.release_credits.return_value = True
    task_id = "0192"

    monkeypatch.setattr(repository, "update_task_status_if_match", AsyncMock(return_value=True))
    invalidate_task = AsyncMock()
    monkeypatch.setattr(cache, "invalidate_task", invalidate_task)

    result = await app_module._handle_restate_handoff_failure(
        pool=pool,
        redis=redis,
        billing=billing,
        task_id=task_id,
        transfer_int=123,
    )
    assert result is None
    billing.release_credits.assert_called_once_with(123)
    invalidate_task.assert_awaited_once_with(redis, task_id)


@pytest.mark.asyncio
async def test_restate_handoff_failure_returns_existing_status_if_not_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MagicMock()
    redis = AsyncMock()
    billing = MagicMock()
    task_id = "0193"

    monkeypatch.setattr(repository, "update_task_status_if_match", AsyncMock(return_value=False))
    monkeypatch.setattr(repository, "get_task_status", AsyncMock(return_value="RUNNING"))

    result = await app_module._handle_restate_handoff_failure(
        pool=pool,
        redis=redis,
        billing=billing,
        task_id=task_id,
        transfer_int=123,
    )
    assert result == "RUNNING"
    billing.release_credits.assert_not_called()


@pytest.mark.asyncio
async def test_restate_handoff_failure_raises_when_release_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MagicMock()
    redis = AsyncMock()
    billing = MagicMock()
    task_id = "0194"

    update_status = AsyncMock(side_effect=[True, True])
    monkeypatch.setattr(repository, "update_task_status_if_match", update_status)
    monkeypatch.setattr(repository, "get_task_status", AsyncMock(return_value=None))
    billing.release_credits.return_value = False

    with pytest.raises(HTTPException, match="Execution orchestration unavailable"):
        await app_module._handle_restate_handoff_failure(
            pool=pool,
            redis=redis,
            billing=billing,
            task_id=task_id,
            transfer_int=456,
        )

    assert update_status.await_count == 2
    assert update_status.await_args_list[0].kwargs["expected_status"] == "PENDING"
    assert update_status.await_args_list[1].kwargs["expected_status"] == "FAILED"
    billing.release_credits.assert_called_once_with(456)
