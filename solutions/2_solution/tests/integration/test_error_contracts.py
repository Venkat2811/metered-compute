from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Generator
from uuid import UUID, uuid4

import httpx
import pytest

from solution2.core.defaults import DEFAULT_ADMIN_API_KEY, DEFAULT_ALICE_API_KEY
from tests.constants import (
    V1_ADMIN_CREDITS_PATH,
    V1_OAUTH_TOKEN_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)

BASE_URL = os.getenv("INTEGRATION_BASE_URL", "http://localhost:8000")
USER1_KEY = os.getenv(
    "INTEGRATION_USER1_API_KEY", os.getenv("ALICE_API_KEY", DEFAULT_ALICE_API_KEY)
)
ADMIN_KEY = os.getenv(
    "INTEGRATION_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
)


@pytest.fixture(scope="module")
def api_client() -> Generator[httpx.Client, None, None]:
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=3.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"integration API is not reachable at {BASE_URL}: {exc}")
    if health.status_code != 200:
        pytest.skip(f"integration API unhealthy at {BASE_URL}: {health.status_code}")

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        yield client


def _metric_value(metrics_text: str, metric_name: str, labels: str) -> float:
    pattern = rf"^{metric_name}\{{{labels}\}} ([0-9\.]+)$"
    match = re.search(pattern, metrics_text, re.MULTILINE)
    if match is None:
        return 0.0
    return float(match.group(1))


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _oauth_token(*, client: httpx.Client, api_key: str, scope: str | None = None) -> str:
    payload: dict[str, str] = {"api_key": api_key}
    if scope is not None:
        payload["scope"] = scope
    response = client.post(V1_OAUTH_TOKEN_PATH, json=payload)
    assert response.status_code == 200, response.text
    token = str(response.json()["access_token"])
    assert token.count(".") == 2
    return token


def _poll_until_terminal(
    *,
    client: httpx.Client,
    access_token: str,
    task_id: str,
    max_attempts: int = 60,
) -> None:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 200:
            status = str(response.json().get("status", ""))
            if status in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
                return
        time.sleep(1)
    raise AssertionError(f"task did not reach terminal state: {task_id}")


@pytest.mark.integration
def test_metrics_include_solution_specific_series(api_client: httpx.Client) -> None:
    metrics = api_client.get("/metrics")
    assert metrics.status_code == 200
    assert "task_submissions_total" in metrics.text
    assert "credit_lua_duration_seconds" in metrics.text
    assert "auth_cache_results_total" in metrics.text
    assert "auth_db_lookups_total" in metrics.text
    assert "stream_consumer_lag" in metrics.text
    assert "stream_pending_entries" in metrics.text
    assert "jwt_validation_duration_seconds" in metrics.text
    assert "snapshot_flush_duration_seconds" in metrics.text
    assert "token_issuance_total" in metrics.text
    assert "pel_recovery_total" in metrics.text


@pytest.mark.integration
def test_jwt_hot_path_avoids_auth_db_and_cache_lookup(api_client: httpx.Client) -> None:
    user_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    pre_metrics = api_client.get("/metrics")
    assert pre_metrics.status_code == 200
    before_db_found = _metric_value(pre_metrics.text, "auth_db_lookups_total", 'result="found"')
    before_cache_hit = _metric_value(pre_metrics.text, "auth_cache_results_total", 'result="hit"')

    missing_task_id = str(uuid4())
    for _ in range(2):
        poll = api_client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": missing_task_id},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert poll.status_code == 404

    post_metrics = api_client.get("/metrics")
    assert post_metrics.status_code == 200
    after_db_found = _metric_value(post_metrics.text, "auth_db_lookups_total", 'result="found"')
    after_cache_hit = _metric_value(post_metrics.text, "auth_cache_results_total", 'result="hit"')

    # JWT path is local verification + revocation check; it should not touch API-key auth cache/DB.
    assert after_db_found - before_db_found == 0.0
    assert after_cache_hit - before_cache_hit == 0.0


@pytest.mark.integration
def test_contract_error_codes_400_401_404_409(api_client: httpx.Client) -> None:
    user_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    bad_request = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user_token}"},
        json={"x": "oops", "y": 1},
    )
    assert bad_request.status_code == 400
    assert bad_request.json()["error"]["code"] == "BAD_REQUEST"

    unauthorized = api_client.post(V1_TASK_SUBMIT_PATH, json={"x": 1, "y": 1})
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"

    not_found = api_client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(uuid4())},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "NOT_FOUND"

    idempotency_key = f"contract-{uuid4()}"
    accepted = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": idempotency_key,
        },
        json={"x": 2, "y": 2},
    )
    assert accepted.status_code == 201
    UUID(str(accepted.json()["task_id"]))

    conflict = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": idempotency_key,
        },
        json={"x": 99, "y": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONFLICT"


@pytest.mark.integration
def test_contract_error_codes_429_and_503(api_client: httpx.Client) -> None:
    user_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    admin_token = _oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    top_up = api_client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": USER1_KEY, "delta": 200, "reason": "integration_burst_topup"},
    )
    assert top_up.status_code == 200

    # Force queue buildup by pausing worker, then exceed per-user active limit.
    stopped = _compose("stop", "worker")
    assert stopped.returncode == 0, stopped.stderr
    accepted_task_ids: list[str] = []
    try:
        statuses: list[int] = []
        for _ in range(9):
            response = api_client.post(
                V1_TASK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Idempotency-Key": f"burst-{uuid4()}",
                },
                json={"x": 3, "y": 4},
            )
            statuses.append(response.status_code)
            if response.status_code in {200, 201}:
                accepted_task_ids.append(str(response.json()["task_id"]))

        assert 429 in statuses
    finally:
        started = _compose("start", "worker")
        assert started.returncode == 0, started.stderr
        for task_id in accepted_task_ids:
            _poll_until_terminal(client=api_client, access_token=user_token, task_id=task_id)

    redis_down = _compose("stop", "redis")
    assert redis_down.returncode == 0, redis_down.stderr
    try:
        degraded = api_client.post(
            V1_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {user_token}"},
            json={"x": 5, "y": 6},
        )
        assert degraded.status_code == 503
        assert degraded.json()["error"]["code"] == "SERVICE_DEGRADED"
    finally:
        redis_up = _compose("start", "redis")
        assert redis_up.returncode == 0, redis_up.stderr
