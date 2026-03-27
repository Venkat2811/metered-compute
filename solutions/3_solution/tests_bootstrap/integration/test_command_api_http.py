from __future__ import annotations

import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"
ADMIN_API_KEY = "e1138140-6c35-49b6-b723-ba8d609d8eb5"
ALICE_API_KEY = "586f0ef6-e655-4413-ab08-a481db150389"
BOB_API_KEY = "c9169bc2-2980-4155-be29-442ffc44ce64"


def _oauth_access_token(*, api_key: str) -> str:
    response = httpx.post(
        f"{BASE_URL}/v1/oauth/token",
        json={"api_key": api_key},
        timeout=10.0,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


@pytest.mark.integration
def test_submit_poll_cancel_and_admin_rbac_contracts() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-{uuid.uuid4()}",
    }

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 1, "y": 2},
        timeout=10.0,
    )
    assert submit.status_code == 201, submit.text
    submit_payload = submit.json()
    task_id = submit_payload["task_id"]
    assert submit_payload["status"] == "PENDING"
    assert submit_payload["billing_state"] == "RESERVED"

    replay = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 1, "y": 2},
        timeout=10.0,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["task_id"] == task_id

    poll = httpx.get(
        f"{BASE_URL}/v1/poll",
        params={"task_id": task_id},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    assert poll.status_code == 200, poll.text
    assert poll.json()["status"] == "PENDING"

    cancel = httpx.post(
        f"{BASE_URL}/v1/task/{task_id}/cancel",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    assert cancel.status_code == 200, cancel.text
    cancel_payload = cancel.json()
    assert cancel_payload["status"] == "CANCELLED"
    assert cancel_payload["billing_state"] == "RELEASED"

    poll_after_cancel = httpx.get(
        f"{BASE_URL}/v1/poll",
        params={"task_id": task_id},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    assert poll_after_cancel.status_code == 200, poll_after_cancel.text
    assert poll_after_cancel.json()["status"] == "CANCELLED"

    admin_credits = httpx.post(
        f"{BASE_URL}/v1/admin/credits",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "api_key": ALICE_API_KEY,
            "amount": 10,
            "reason": "integration-test",
        },
        timeout=10.0,
    )
    assert admin_credits.status_code == 403, admin_credits.text


@pytest.mark.integration
def test_admin_can_topup_user_balance() -> None:
    admin_token = _oauth_access_token(api_key=ADMIN_API_KEY)
    headers = {"Authorization": f"Bearer {admin_token}"}

    first = httpx.post(
        f"{BASE_URL}/v1/admin/credits",
        headers=headers,
        json={
            "api_key": BOB_API_KEY,
            "amount": 7,
            "reason": "integration-topup-1",
        },
        timeout=10.0,
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["api_key"] == BOB_API_KEY
    assert isinstance(first_payload["new_balance"], int)

    second = httpx.post(
        f"{BASE_URL}/v1/admin/credits",
        headers=headers,
        json={
            "api_key": BOB_API_KEY,
            "amount": 13,
            "reason": "integration-topup-2",
        },
        timeout=10.0,
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["api_key"] == BOB_API_KEY
    assert second_payload["new_balance"] == first_payload["new_balance"] + 13
