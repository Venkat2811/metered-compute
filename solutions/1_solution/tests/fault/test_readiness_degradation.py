from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from solution1.core.defaults import DEFAULT_USER1_API_KEY
from tests.constants import V1_OAUTH_TOKEN_PATH

BASE_URL = os.getenv("FAULT_BASE_URL", "http://localhost:8000")
USER1_KEY = os.getenv("FAULT_USER1_API_KEY", os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY))


def _compose(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def _ready_status() -> int | None:
    try:
        response = httpx.get(f"{BASE_URL}/ready", timeout=3.0)
    except httpx.HTTPError:
        return None
    return response.status_code


def _oauth_token(*, api_key: str) -> str:
    response = httpx.post(
        f"{BASE_URL}{V1_OAUTH_TOKEN_PATH}",
        timeout=5.0,
        json={"api_key": api_key},
    )
    assert response.status_code == 200, response.text
    token = str(response.json()["access_token"])
    assert token.count(".") == 2
    return token


@pytest.mark.fault
def test_ready_degrades_when_redis_is_down_and_recovers() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"fault API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"fault API unhealthy at {BASE_URL}: {health.status_code}")

    project_root = Path(__file__).resolve().parents[2]
    user_token = _oauth_token(api_key=USER1_KEY)

    stop = _compose(project_root, "stop", "redis")
    if stop.returncode != 0:
        pytest.skip(f"unable to stop redis service for fault test: {stop.stderr}")

    try:
        degraded = False
        for _ in range(20):
            status_code = _ready_status()
            if status_code == 503:
                degraded = True
                break
            time.sleep(0.5)
        assert degraded, "ready endpoint did not degrade when redis was stopped"
    finally:
        start = _compose(project_root, "start", "redis")
        assert start.returncode == 0, start.stderr

        recovered = False
        for _ in range(30):
            status_code = _ready_status()
            if status_code == 200:
                recovered = True
                break
            # Trigger Lua auto-reload path after Redis restart.
            httpx.post(
                f"{BASE_URL}/v1/task",
                timeout=5.0,
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Idempotency-Key": f"fault-recover-{uuid4()}",
                },
                json={"x": 1, "y": 1},
            )
            time.sleep(0.5)
        assert recovered, "ready endpoint did not recover after redis restart"

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        timeout=5.0,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": f"fault-{uuid4()}",
        },
        json={"x": 2, "y": 3},
    )
    # Core assertion for this fault: redis script cache loss must not create 500s.
    assert submit.status_code in {201, 402, 429}
