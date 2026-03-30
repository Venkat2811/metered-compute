from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import tigerbeetle as tb

from solution4.billing import Billing

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
ALICE_ID = "a0000000-0000-0000-0000-000000000001"
ALICE_KEY = "sk-alice-secret-key-001"
TERMINAL_STATES = {"FAILED", "COMPLETED", "CANCELLED"}


def _compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _wait_for_health(url: str, *, timeout_seconds: float = 60.0) -> None:
    deadline = time.time() + timeout_seconds
    with httpx.Client(timeout=5) as client:
        while time.time() < deadline:
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise AssertionError(f"timed out waiting for healthy endpoint: {url}")


def _billing() -> Billing:
    client = tb.client.ClientSync(cluster_id=0, replica_addresses="127.0.0.1:3000")
    return Billing(
        client=client,
        revenue_id=1_000_001,
        escrow_id=1_000_002,
        timeout_secs=300,
    )


@pytest.mark.skipif(
    not os.environ.get("FAULT"),
    reason="Set FAULT=1 to run fault tests",
)
def test_compute_outage_releases_credits_immediately() -> None:
    billing = _billing()
    before_balance = billing.get_balance(ALICE_ID)

    with httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {ALICE_KEY}"}, timeout=10) as client:
        try:
            _compose("stop", "compute")

            submit = client.post("/v1/task", json={"x": 7, "y": 8})
            assert submit.status_code == 201
            task_id = submit.json()["task_id"]

            for _ in range(30):
                poll = client.get("/v1/poll", params={"task_id": task_id})
                assert poll.status_code == 200
                status = poll.json()["status"]
                if status in TERMINAL_STATES:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("task did not reach terminal state during compute outage")

            assert status == "FAILED"
            after_balance = billing.get_balance(ALICE_ID)
            assert after_balance == before_balance
        finally:
            _compose("up", "-d", "compute")
            _wait_for_health("http://localhost:8001/health")
            _wait_for_health(f"{BASE_URL}/ready")
