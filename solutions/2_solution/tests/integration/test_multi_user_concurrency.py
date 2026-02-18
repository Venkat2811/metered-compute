from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx
import pytest

from solution2.constants import SubscriptionTier, max_concurrent_for_tier
from solution2.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_USER1_API_KEY,
    DEFAULT_USER2_API_KEY,
)
from tests.constants import (
    COMPAT_TASK_POLL_PATH,
    COMPAT_TASK_SUBMIT_PATH,
    V1_ADMIN_CREDITS_PATH,
    V1_OAUTH_TOKEN_PATH,
    V1_TASK_POLL_PATH,
    V1_TASK_SUBMIT_PATH,
)

BASE_URL = os.getenv("INTEGRATION_BASE_URL", "http://localhost:8000")
USER1_KEY = os.getenv(
    "INTEGRATION_USER1_API_KEY", os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY)
)
USER2_KEY = os.getenv(
    "INTEGRATION_USER2_API_KEY", os.getenv("BOB_API_KEY", DEFAULT_USER2_API_KEY)
)
ADMIN_KEY = os.getenv(
    "INTEGRATION_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
)


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


def _set_balance(
    client: httpx.Client,
    *,
    admin_token: str,
    api_key: str,
    target: int,
    reason: str,
) -> int:
    probe = client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"api_key": api_key, "delta": 0, "reason": f"{reason}_probe"},
    )
    assert probe.status_code == 200
    current = int(probe.json()["new_balance"])
    delta = target - current
    if delta:
        update = client.post(
            V1_ADMIN_CREDITS_PATH,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"api_key": api_key, "delta": delta, "reason": f"{reason}_set"},
        )
        assert update.status_code == 200
        return int(update.json()["new_balance"])
    return current


def _poll_terminal(client: httpx.Client, *, task_id: str, access_token: str) -> dict[str, Any]:
    for _ in range(60):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 200:
            payload: dict[str, Any] = response.json()
            if payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
                return payload
        time.sleep(1)
    raise AssertionError(f"task did not reach terminal status: {task_id}")


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


@pytest.mark.integration
def test_assignment_compat_endpoints_submit_and_poll(api_client: httpx.Client) -> None:
    admin_token = _oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    user1_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    _set_balance(
        api_client,
        admin_token=admin_token,
        api_key=USER1_KEY,
        target=120,
        reason="compat_endpoints",
    )

    task_id = ""
    for _ in range(20):
        submit = api_client.post(
            COMPAT_TASK_SUBMIT_PATH,
            headers={"Authorization": f"Bearer {user1_token}"},
            json={"x": 20, "y": 22},
        )
        if submit.status_code == 201:
            task_id = str(submit.json()["task_id"])
            break

        retryable_codes = {"TOO_MANY_REQUESTS", "SERVICE_DEGRADED"}
        payload = submit.json()
        error_code = payload.get("error", {}).get("code")
        assert error_code in retryable_codes, (
            f"unexpected submit response: status={submit.status_code} payload={payload}"
        )
        time.sleep(1)

    assert task_id

    terminal: dict[str, Any] | None = None
    for _ in range(40):
        poll = api_client.get(
            COMPAT_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert poll.status_code == 200
        payload = poll.json()
        if payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
            terminal = payload
            break
        time.sleep(1)

    assert terminal is not None
    assert terminal["status"] == "COMPLETED"
    assert terminal["result"] == {"z": 42}


@pytest.mark.integration
def test_multi_user_concurrency_enforced_per_user(api_client: httpx.Client) -> None:
    admin_token = _oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    user1_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    user2_token = _oauth_token(client=api_client, api_key=USER2_KEY)
    _set_balance(
        api_client,
        admin_token=admin_token,
        api_key=USER1_KEY,
        target=300,
        reason="multi_user_concurrency_u1",
    )
    _set_balance(
        api_client,
        admin_token=admin_token,
        api_key=USER2_KEY,
        target=300,
        reason="multi_user_concurrency_u2",
    )

    stopped = _compose("stop", "worker")
    assert stopped.returncode == 0, stopped.stderr

    accepted: dict[str, list[str]] = {user1_token: [], user2_token: []}
    counts: dict[str, dict[int, int]] = {user1_token: {}, user2_token: {}}
    try:

        def _submit(user_key: str, idx: int) -> tuple[str, int, str | None]:
            response = api_client.post(
                V1_TASK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {user_key}",
                    "Idempotency-Key": f"it-concurrency-{idx}-{uuid4()}",
                },
                json={"x": 3, "y": 4},
            )
            task_id = None
            if response.status_code in (200, 201):
                task_id = str(response.json()["task_id"])
            return user_key, response.status_code, task_id

        submissions: list[tuple[str, int, str | None]] = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for idx in range(10):
                futures.append(executor.submit(_submit, user1_token, idx))
                futures.append(executor.submit(_submit, user2_token, idx))
            for future in futures:
                submissions.append(future.result())

        for user_key, status, task_id in submissions:
            counts[user_key][status] = counts[user_key].get(status, 0) + 1
            if task_id is not None:
                accepted[user_key].append(task_id)

        expected_caps = {
            user1_token: max_concurrent_for_tier(
                base_max_concurrent=int(DEFAULT_MAX_CONCURRENT),
                tier=SubscriptionTier.PRO,
            ),
            user2_token: max_concurrent_for_tier(
                base_max_concurrent=int(DEFAULT_MAX_CONCURRENT),
                tier=SubscriptionTier.FREE,
            ),
        }

        for user_key in (user1_token, user2_token):
            accepted_count = len(accepted[user_key])
            rejected_429 = counts[user_key].get(429, 0)
            unexpected = {
                code: count
                for code, count in counts[user_key].items()
                if code not in {200, 201, 429}
            }
            assert not unexpected, f"unexpected statuses for {user_key}: {unexpected}"
            assert accepted_count <= expected_caps[user_key]
            assert rejected_429 >= 1
    finally:
        started = _compose("start", "worker")
        assert started.returncode == 0, started.stderr
        for task_id in accepted[user1_token]:
            _poll_terminal(api_client, task_id=task_id, access_token=user1_token)
        for task_id in accepted[user2_token]:
            _poll_terminal(api_client, task_id=task_id, access_token=user2_token)


@pytest.mark.integration
def test_idempotency_key_is_scoped_per_user(api_client: httpx.Client) -> None:
    admin_token = _oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    user1_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    user2_token = _oauth_token(client=api_client, api_key=USER2_KEY)
    _set_balance(
        api_client,
        admin_token=admin_token,
        api_key=USER1_KEY,
        target=200,
        reason="idem_scope_u1",
    )
    _set_balance(
        api_client,
        admin_token=admin_token,
        api_key=USER2_KEY,
        target=200,
        reason="idem_scope_u2",
    )

    shared_key = f"shared-idem-{uuid4()}"
    body = {"x": 4, "y": 5}

    submit_u1 = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user1_token}",
            "Idempotency-Key": shared_key,
        },
        json=body,
    )
    assert submit_u1.status_code == 201, submit_u1.text
    task1 = str(submit_u1.json()["task_id"])

    submit_u2 = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user2_token}",
            "Idempotency-Key": shared_key,
        },
        json=body,
    )
    assert submit_u2.status_code == 201, submit_u2.text
    task2 = str(submit_u2.json()["task_id"])
    assert task1 != task2

    terminal_u1 = _poll_terminal(api_client, task_id=task1, access_token=user1_token)
    terminal_u2 = _poll_terminal(api_client, task_id=task2, access_token=user2_token)
    assert terminal_u1["status"] == "COMPLETED"
    assert terminal_u2["status"] == "COMPLETED"
