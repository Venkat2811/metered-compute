from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from solution2.api.paths import V1_AUTH_REVOKE_PATH, V1_OAUTH_TOKEN_PATH
from solution2.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_BOB_API_KEY,
)
from tests.constants import (
    V1_ADMIN_CREDITS_PATH,
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


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _metric_value(metrics_text: str, metric_name: str, labels: str) -> float:
    line_prefix = f"{metric_name}{{{labels}}} "
    for line in metrics_text.splitlines():
        if line.startswith(line_prefix):
            value = line.removeprefix(line_prefix).strip()
            return float(value)
    return 0.0


def _get_oauth_token(
    *,
    client: httpx.Client,
    api_key: str,
    scope: str | None = None,
) -> str:
    payload: dict[str, str] = {"api_key": api_key}
    if scope is not None:
        payload["scope"] = scope
    response = client.post(V1_OAUTH_TOKEN_PATH, json=payload)
    assert response.status_code == 200, response.text
    token = str(response.json()["access_token"])
    assert token.count(".") == 2
    return token


def _set_balance(*, client: httpx.Client, admin_token: str, api_key: str, target: int) -> None:
    headers = {"Authorization": f"Bearer {admin_token}"}
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers=headers,
        json={"api_key": api_key, "delta": 0, "reason": "oauth_probe"},
    )
    assert probe.status_code == 200, probe.text
    current = int(probe.json()["new_balance"])
    delta = target - current
    if delta == 0:
        return
    update = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers=headers,
        json={"api_key": api_key, "delta": delta, "reason": "oauth_set"},
    )
    assert update.status_code == 200, update.text
    assert int(update.json()["new_balance"]) == target


def _get_balance(*, client: httpx.Client, admin_token: str, api_key: str) -> int:
    response = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": api_key, "delta": 0, "reason": "oauth_balance_probe"},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["new_balance"])


def _poll_terminal(*, client: httpx.Client, token: str, task_id: str) -> dict[str, Any]:
    for _ in range(30):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        payload: dict[str, Any] = response.json()
        if payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
            return payload
        time.sleep(1)
    raise AssertionError(f"task did not become terminal: {task_id}")


def _revoke_token(*, client: httpx.Client, token: str) -> None:
    response = client.post(
        V1_AUTH_REVOKE_PATH,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"revoked": True}


@pytest.mark.integration
def test_oauth_jwt_can_call_protected_endpoints(api_client: httpx.Client) -> None:
    admin_token = _get_oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    user_token = _get_oauth_token(client=api_client, api_key=USER1_KEY)

    _set_balance(client=api_client, admin_token=admin_token, api_key=USER1_KEY, target=200)

    submit = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": f"oauth-jwt-{uuid4()}",
        },
        json={"x": 8, "y": 13},
    )
    assert submit.status_code == 201, submit.text
    task_id = str(submit.json()["task_id"])
    assert UUID(task_id).version == 7

    terminal = _poll_terminal(client=api_client, token=user_token, task_id=task_id)
    assert terminal["status"] == "COMPLETED"
    assert terminal["result"] == {"z": 21}


@pytest.mark.integration
def test_revoked_jwt_is_rejected(api_client: httpx.Client) -> None:
    user_token = _get_oauth_token(client=api_client, api_key=USER1_KEY)
    _revoke_token(client=api_client, token=user_token)

    response = api_client.get(
        V1_TASK_POLL_PATH,
        params={"task_id": str(uuid4())},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 401
    payload = response.json()["error"]
    assert payload["code"] == "UNAUTHORIZED"


@pytest.mark.integration
def test_jwt_auth_cache_hot_path_limits_db_lookups(api_client: httpx.Client) -> None:
    user_token = _get_oauth_token(client=api_client, api_key=USER1_KEY)

    pre_metrics = api_client.get("/metrics")
    assert pre_metrics.status_code == 200
    before_db_found = _metric_value(pre_metrics.text, "auth_db_lookups_total", 'result="found"')

    missing_task_id = str(uuid4())
    headers = {"Authorization": f"Bearer {user_token}"}
    for _ in range(2):
        poll = api_client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": missing_task_id},
            headers=headers,
        )
        assert poll.status_code == 404

    post_metrics = api_client.get("/metrics")
    assert post_metrics.status_code == 200
    after_db_found = _metric_value(post_metrics.text, "auth_db_lookups_total", 'result="found"')
    assert after_db_found - before_db_found == 0.0


@pytest.mark.integration
def test_missing_scope_returns_forbidden(api_client: httpx.Client) -> None:
    poll_only_token = _get_oauth_token(
        client=api_client,
        api_key=USER1_KEY,
        scope="task:poll",
    )
    submit = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {poll_only_token}",
            "Idempotency-Key": f"scope-miss-{uuid4()}",
        },
        json={"x": 3, "y": 4},
    )
    assert submit.status_code == 403, submit.text
    payload = submit.json()["error"]
    assert payload["code"] == "FORBIDDEN"


@pytest.mark.integration
def test_jwt_tier_based_concurrency_envelopes(api_client: httpx.Client) -> None:
    admin_token = _get_oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    pro_user_token = _get_oauth_token(client=api_client, api_key=USER1_KEY)

    # The third seeded user is mapped to solution2-user2/free tier in dev defaults.
    free_user_api_key = os.getenv(
        "INTEGRATION_USER2_API_KEY",
        os.getenv("BOB_API_KEY", DEFAULT_BOB_API_KEY),
    )
    free_user_token = _get_oauth_token(client=api_client, api_key=free_user_api_key)

    _set_balance(client=api_client, admin_token=admin_token, api_key=USER1_KEY, target=800)
    _set_balance(
        client=api_client,
        admin_token=admin_token,
        api_key=free_user_api_key,
        target=800,
    )

    stopped = _compose("stop", "worker")
    assert stopped.returncode == 0, stopped.stderr

    accepted: dict[str, list[str]] = {"pro": [], "free": []}
    status_counts: dict[str, dict[int, int]] = {"pro": {}, "free": {}}

    try:

        def _submit(token: str, lane: str, idx: int) -> tuple[str, int, str | None]:
            response = api_client.post(
                V1_TASK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": f"jwt-tier-{lane}-{idx}-{uuid4()}",
                },
                json={"x": 4, "y": 5, "model_class": "small"},
            )
            task_id = None
            if response.status_code in {200, 201}:
                task_id = str(response.json()["task_id"])
            return lane, response.status_code, task_id

        submissions: list[tuple[str, int, str | None]] = []
        with ThreadPoolExecutor(max_workers=24) as executor:
            futures = []
            for idx in range(12):
                futures.append(executor.submit(_submit, pro_user_token, "pro", idx))
                futures.append(executor.submit(_submit, free_user_token, "free", idx))
            for future in futures:
                submissions.append(future.result())

        for lane, status_code, task_id in submissions:
            status_counts[lane][status_code] = status_counts[lane].get(status_code, 0) + 1
            if task_id is not None:
                accepted[lane].append(task_id)

        unexpected_pro = {
            code: count
            for code, count in status_counts["pro"].items()
            if code not in {200, 201, 429}
        }
        unexpected_free = {
            code: count
            for code, count in status_counts["free"].items()
            if code not in {200, 201, 429}
        }
        assert not unexpected_pro, unexpected_pro
        assert not unexpected_free, unexpected_free

        pro_accepts = len(accepted["pro"])
        free_accepts = len(accepted["free"])
        assert pro_accepts <= 6
        assert free_accepts <= 3
        assert pro_accepts > free_accepts
        assert status_counts["pro"].get(429, 0) >= 1
        assert status_counts["free"].get(429, 0) >= 1
    finally:
        started = _compose("start", "worker")
        assert started.returncode == 0, started.stderr
        for task_id in accepted["pro"]:
            _poll_terminal(client=api_client, token=pro_user_token, task_id=task_id)
        for task_id in accepted["free"]:
            _poll_terminal(client=api_client, token=free_user_token, task_id=task_id)


@pytest.mark.integration
def test_jwt_model_class_cost_multipliers(api_client: httpx.Client) -> None:
    admin_token = _get_oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    user_token = _get_oauth_token(client=api_client, api_key=USER1_KEY)

    _set_balance(client=api_client, admin_token=admin_token, api_key=USER1_KEY, target=500)
    before = _get_balance(client=api_client, admin_token=admin_token, api_key=USER1_KEY)

    small_submit = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": f"jwt-cost-small-{uuid4()}",
        },
        json={"x": 1, "y": 1, "model_class": "small"},
    )
    assert small_submit.status_code == 201, small_submit.text
    assert small_submit.json()["estimated_seconds"] == 2
    small_task = str(small_submit.json()["task_id"])
    small_terminal = _poll_terminal(client=api_client, token=user_token, task_id=small_task)
    assert small_terminal["status"] == "COMPLETED"
    after_small = _get_balance(client=api_client, admin_token=admin_token, api_key=USER1_KEY)

    large_submit = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": f"jwt-cost-large-{uuid4()}",
        },
        json={"x": 2, "y": 2, "model_class": "large"},
    )
    assert large_submit.status_code == 201, large_submit.text
    assert large_submit.json()["estimated_seconds"] == 7
    large_task = str(large_submit.json()["task_id"])
    large_terminal = _poll_terminal(client=api_client, token=user_token, task_id=large_task)
    assert large_terminal["status"] == "COMPLETED"
    after_large = _get_balance(client=api_client, admin_token=admin_token, api_key=USER1_KEY)

    assert before - after_small == 10
    assert after_small - after_large == 50
