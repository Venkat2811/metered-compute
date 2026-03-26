from __future__ import annotations

import httpx
import pytest


@pytest.mark.integration
def test_running_stack_serves_health_and_ready() -> None:
    health = httpx.get("http://localhost:8000/health", timeout=10.0)
    ready = httpx.get("http://localhost:8000/ready", timeout=10.0)

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
