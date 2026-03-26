from __future__ import annotations

import time
import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"
ALICE_API_KEY = "586f0ef6-e655-4413-ab08-a481db150389"


def _oauth_access_token(*, api_key: str) -> str:
    response = httpx.post(
        f"{BASE_URL}/v1/oauth/token",
        json={"api_key": api_key},
        timeout=10.0,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


@pytest.mark.integration
def test_submit_completes_over_live_worker_path() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-complete-{uuid.uuid4()}",
    }

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 2, "y": 3},
        timeout=10.0,
    )
    assert submit.status_code == 201, submit.text
    task_id = submit.json()["task_id"]

    deadline = time.time() + 30.0
    final_payload: dict[str, object] | None = None
    while time.time() < deadline:
        poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert poll.status_code == 200, poll.text
        payload = poll.json()
        if payload["status"] in {"COMPLETED", "FAILED"}:
            final_payload = payload
            break
        time.sleep(0.5)

    assert final_payload is not None
    assert final_payload["status"] == "COMPLETED"
    assert final_payload["billing_state"] == "CAPTURED"
    assert final_payload["error"] is None
