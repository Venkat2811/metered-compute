from __future__ import annotations

import os
import time
from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from solution1.core.defaults import (
    DEFAULT_ADMIN_API_KEY,
    DEFAULT_ALICE_API_KEY,
    DEFAULT_BOB_API_KEY,
)
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
USER2_KEY = os.getenv(
    "INTEGRATION_USER2_API_KEY", os.getenv("BOB_API_KEY", DEFAULT_BOB_API_KEY)
)
ADMIN_KEY = os.getenv(
    "INTEGRATION_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", DEFAULT_ADMIN_API_KEY)
)


def _poll_until_terminal(
    client: httpx.Client,
    *,
    task_id: str,
    access_token: str,
    max_attempts: int = 40,
) -> dict[str, Any]:
    for _ in range(max_attempts):
        response = client.get(
            V1_TASK_POLL_PATH,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
            return payload
        # Worker simulation is deterministic, a short poll interval is enough.
        time.sleep(1)
    raise AssertionError("task did not reach a terminal status")


def _oauth_token(*, client: httpx.Client, api_key: str, scope: str | None = None) -> str:
    payload: dict[str, str] = {"api_key": api_key}
    if scope is not None:
        payload["scope"] = scope
    response = client.post(V1_OAUTH_TOKEN_PATH, json=payload)
    assert response.status_code == 200, response.text
    token = str(response.json()["access_token"])
    assert token.count(".") == 2
    return token


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
def test_health_ready_and_metrics_endpoints(api_client: httpx.Client) -> None:
    health = api_client.get("/health")
    ready = api_client.get("/ready")
    metrics = api_client.get("/metrics")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert metrics.status_code == 200
    assert "process_cpu_seconds_total" in metrics.text


@pytest.mark.integration
def test_submit_poll_and_idempotent_replay(api_client: httpx.Client) -> None:
    user_token = _oauth_token(client=api_client, api_key=USER1_KEY)
    idempotency = f"it-{uuid4()}"
    submit_payload = {"x": 12, "y": 30}

    first = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": idempotency,
        },
        json=submit_payload,
    )
    assert first.status_code == 201
    first_json = first.json()
    task_id = str(first_json["task_id"])
    assert UUID(task_id).version == 7
    assert first_json["estimated_seconds"] == 2

    second = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={
            "Authorization": f"Bearer {user_token}",
            "Idempotency-Key": idempotency,
        },
        json=submit_payload,
    )
    assert second.status_code == 200
    second_json = second.json()
    assert str(second_json["task_id"]) == task_id
    assert second_json["estimated_seconds"] == 2

    terminal = _poll_until_terminal(api_client, task_id=task_id, access_token=user_token)
    assert terminal["status"] == "COMPLETED"
    assert terminal["result"] == {"z": 42}


@pytest.mark.integration
def test_admin_credit_update_and_insufficient_credits(api_client: httpx.Client) -> None:
    admin_token = _oauth_token(
        client=api_client,
        api_key=ADMIN_KEY,
        scope="task:submit task:poll task:cancel admin:credits",
    )
    user2_token = _oauth_token(client=api_client, api_key=USER2_KEY)
    probe = api_client.post(
        V1_ADMIN_CREDITS_PATH,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "api_key": USER2_KEY,
            "delta": 0,
            "reason": "integration_probe",
        },
    )
    assert probe.status_code == 200
    current_balance = int(probe.json()["new_balance"])

    if current_balance > 5:
        debit = api_client.post(
            V1_ADMIN_CREDITS_PATH,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "api_key": USER2_KEY,
                "delta": -(current_balance - 5),
                "reason": "integration_debit",
            },
        )
        assert debit.status_code == 200

    submit = api_client.post(
        V1_TASK_SUBMIT_PATH,
        headers={"Authorization": f"Bearer {user2_token}"},
        json={"x": 1, "y": 2},
    )
    assert submit.status_code == 402
    error = submit.json()["error"]
    assert error["code"] == "INSUFFICIENT_CREDITS"
