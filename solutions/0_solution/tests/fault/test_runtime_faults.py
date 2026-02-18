from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from solution0.core.defaults import DEFAULT_USER1_API_KEY

BASE_URL = os.getenv("FAULT_BASE_URL", "http://localhost:8000")
USER1_KEY = os.getenv("FAULT_USER1_API_KEY", os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY))


def _compose(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _wait_ready(status_code: int, attempts: int = 30) -> bool:
    for _ in range(attempts):
        try:
            ready = httpx.get(f"{BASE_URL}/ready", timeout=3.0)
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        if ready.status_code == status_code:
            return True
        time.sleep(0.5)
    return False


@pytest.mark.fault
def test_worker_crash_allows_cancel_path() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"fault API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"fault API unhealthy at {BASE_URL}: {health.status_code}")

    project_root = Path(__file__).resolve().parents[2]
    stopped = _compose(project_root, "stop", "worker")
    assert stopped.returncode == 0, stopped.stderr

    try:
        submit = httpx.post(
            f"{BASE_URL}/v1/task",
            timeout=5.0,
            headers={
                "Authorization": f"Bearer {USER1_KEY}",
                "Idempotency-Key": f"worker-crash-{uuid4()}",
            },
            json={"x": 9, "y": 1},
        )
        assert submit.status_code == 201
        task_id = submit.json()["task_id"]

        cancel = httpx.post(
            f"{BASE_URL}/v1/task/{task_id}/cancel",
            timeout=5.0,
            headers={"Authorization": f"Bearer {USER1_KEY}"},
        )
        assert cancel.status_code == 200

        poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            timeout=5.0,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {USER1_KEY}"},
        )
        assert poll.status_code == 200
        assert poll.json()["status"] == "CANCELLED"
    finally:
        started = _compose(project_root, "start", "worker")
        assert started.returncode == 0, started.stderr


@pytest.mark.fault
def test_postgres_down_readiness_and_submit_degrade() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"fault API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"fault API unhealthy at {BASE_URL}: {health.status_code}")

    # Warm auth cache for controlled degradation checks.
    warm = httpx.get(
        f"{BASE_URL}/v1/poll",
        timeout=5.0,
        params={"task_id": str(uuid4())},
        headers={"Authorization": f"Bearer {USER1_KEY}"},
    )
    assert warm.status_code == 404

    project_root = Path(__file__).resolve().parents[2]
    stopped = _compose(project_root, "stop", "postgres")
    assert stopped.returncode == 0, stopped.stderr

    try:
        assert _wait_ready(503), "ready endpoint did not degrade when postgres stopped"

        degraded = httpx.post(
            f"{BASE_URL}/v1/task",
            timeout=5.0,
            headers={"Authorization": f"Bearer {USER1_KEY}"},
            json={"x": 4, "y": 4},
        )
        assert degraded.status_code == 503
        assert degraded.json()["error"]["code"] == "SERVICE_DEGRADED"
    finally:
        started = _compose(project_root, "start", "postgres")
        assert started.returncode == 0, started.stderr
        assert _wait_ready(200), "ready endpoint did not recover after postgres restart"
