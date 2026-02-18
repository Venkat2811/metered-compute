from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")


@pytest.mark.e2e
def test_demo_script_reaches_terminal_state() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"e2e API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"e2e API unhealthy at {BASE_URL}: {health.status_code}")

    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "utils" / "demo.sh"

    completed = subprocess.run(
        [str(script_path)],
        cwd=project_root,
        env={**os.environ, "BASE_URL": BASE_URL},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "submit:" in completed.stdout
    assert "poll:" in completed.stdout
    assert '"status":"COMPLETED"' in completed.stdout
