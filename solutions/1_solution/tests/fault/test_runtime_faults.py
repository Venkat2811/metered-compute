from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from solution1.core.defaults import DEFAULT_ALICE_API_KEY
from tests.constants import V1_AUTH_REVOKE_PATH, V1_OAUTH_TOKEN_PATH, V1_TASK_POLL_PATH

BASE_URL = os.getenv("FAULT_BASE_URL", "http://localhost:8000")
USER1_KEY = os.getenv("FAULT_USER1_API_KEY", os.getenv("ALICE_API_KEY", DEFAULT_ALICE_API_KEY))


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
def test_worker_crash_allows_cancel_path() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"fault API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"fault API unhealthy at {BASE_URL}: {health.status_code}")

    project_root = Path(__file__).resolve().parents[2]
    user_token = _oauth_token(api_key=USER1_KEY)
    stopped = _compose(project_root, "stop", "worker")
    assert stopped.returncode == 0, stopped.stderr

    try:
        submit = httpx.post(
            f"{BASE_URL}/v1/task",
            timeout=5.0,
            headers={
                "Authorization": f"Bearer {user_token}",
                "Idempotency-Key": f"worker-crash-{uuid4()}",
            },
            json={"x": 9, "y": 1},
        )
        assert submit.status_code == 201
        task_id = submit.json()["task_id"]

        cancel = httpx.post(
            f"{BASE_URL}/v1/task/{task_id}/cancel",
            timeout=5.0,
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert cancel.status_code == 200

        poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            timeout=5.0,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {user_token}"},
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
    user_token = _oauth_token(api_key=USER1_KEY)
    warm = httpx.get(
        f"{BASE_URL}/v1/poll",
        timeout=5.0,
        params={"task_id": str(uuid4())},
        headers={"Authorization": f"Bearer {user_token}"},
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
            headers={"Authorization": f"Bearer {user_token}"},
            json={"x": 4, "y": 4},
        )
        assert degraded.status_code == 503
        assert degraded.json()["error"]["code"] == "SERVICE_DEGRADED"
    finally:
        started = _compose(project_root, "start", "postgres")
        assert started.returncode == 0, started.stderr
        assert _wait_ready(200), "ready endpoint did not recover after postgres restart"


@pytest.mark.fault
def test_revocation_survives_redis_restart_with_pg_fallback() -> None:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"fault API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"fault API unhealthy at {BASE_URL}: {health.status_code}")

    project_root = Path(__file__).resolve().parents[2]
    user_token = _oauth_token(api_key=USER1_KEY)

    revoke_response = httpx.post(
        f"{BASE_URL}{V1_AUTH_REVOKE_PATH}",
        timeout=5.0,
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert revoke_response.status_code == 200, revoke_response.text
    assert revoke_response.json() == {"revoked": True}

    stopped = _compose(project_root, "stop", "redis")
    assert stopped.returncode == 0, stopped.stderr

    try:
        # Redis is unavailable, so auth must use Postgres revocation fallback.
        while_redis_down = httpx.get(
            f"{BASE_URL}{V1_TASK_POLL_PATH}",
            timeout=5.0,
            params={"task_id": str(uuid4())},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert while_redis_down.status_code == 401, while_redis_down.text
    finally:
        started = _compose(project_root, "start", "redis")
        assert started.returncode == 0, started.stderr

    restarted_api = _compose(project_root, "restart", "api")
    assert restarted_api.returncode == 0, restarted_api.stderr
    assert _wait_ready(200), "ready endpoint did not recover after API restart"

    after_restart = httpx.get(
        f"{BASE_URL}{V1_TASK_POLL_PATH}",
        timeout=5.0,
        params={"task_id": str(uuid4())},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert after_restart.status_code == 401, after_restart.text
