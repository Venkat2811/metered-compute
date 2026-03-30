from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from solution1.core.defaults import DEFAULT_ALICE_API_KEY

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
USER1_KEY = os.getenv("E2E_USER1_API_KEY", os.getenv("ALICE_API_KEY", DEFAULT_ALICE_API_KEY))


@pytest.mark.e2e
def test_python_demo_script_reaches_completed_state() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"e2e API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"e2e API unhealthy at {BASE_URL}: {health.status_code}")

    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "utils" / "demo.py"

    completed = subprocess.run(
        [sys.executable, str(script_path), "--base-url", BASE_URL, "--api-key", USER1_KEY],
        cwd=project_root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "submit[" in completed.stdout
    assert "poll[" in completed.stdout
