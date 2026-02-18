from __future__ import annotations

import json

import pytest

from solution1.utils.logging import configure_logging, get_logger


def test_structured_log_contains_required_keys(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()
    logger = get_logger("test")
    logger.info("task_event", task_id="task-1", user_id="user-1", trace_id="trace-1")

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])

    assert payload["event"] == "task_event"
    assert payload["task_id"] == "task-1"
    assert payload["user_id"] == "user-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["level"] == "info"
    assert "timestamp" in payload
