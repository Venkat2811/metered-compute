"""Integration tests — require docker compose up.

Run with: INTEGRATION=1 pytest tests/integration -v
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx
import pytest

BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
COMPUTE_BASE = os.environ.get("COMPUTE_BASE_URL", "http://localhost:8001")
PROMETHEUS_BASE = os.environ.get("PROMETHEUS_BASE_URL", "http://localhost:9090")
API_KEY = "sk-alice-secret-key-001"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, headers=HEADERS, timeout=10)


@pytest.mark.skipif(
    not os.environ.get("INTEGRATION"),
    reason="Set INTEGRATION=1 to run integration tests",
)
class TestSubmitFlow:
    def test_health(self, client: httpx.Client) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_submit_and_poll(self, client: httpx.Client) -> None:
        # Submit
        r = client.post("/v1/task", json={"x": 3, "y": 4})
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "PENDING"
        task_id = data["task_id"]

        # Poll (may need retries while Restate processes)
        for _ in range(20):
            r = client.get("/v1/poll", params={"task_id": task_id})
            assert r.status_code == 200
            if r.json().get("status") == "COMPLETED":
                break
            time.sleep(0.5)
        else:
            pytest.fail("Task did not complete within timeout")

    def test_submit_and_cancel(self, client: httpx.Client) -> None:
        r = client.post("/v1/task", json={"x": 1, "y": 1})
        assert r.status_code == 201
        task_id = r.json()["task_id"]

        # Cancel immediately (before Restate picks it up)
        r = client.post(f"/v1/task/{task_id}/cancel")
        # May be immediate cancelled, deferred cancel, or already terminal.
        assert r.status_code in (200, 409)
        if r.status_code == 200:
            status = r.json().get("status")
            assert status in {"CANCELLED", "CANCEL_REQUESTED"}
            assert int(r.json()["credits_refunded"]) in (0, 10)
            if status == "CANCEL_REQUESTED":
                # eventual terminal should still become canceled
                for _ in range(20):
                    poll = client.get("/v1/poll", params={"task_id": task_id})
                    assert poll.status_code == 200
                    final_status = poll.json().get("status")
                    if final_status == "CANCELLED":
                        break
                    if final_status in {"FAILED", "COMPLETED"}:
                        raise AssertionError(f"task reached unexpected terminal state: {final_status}")
                    time.sleep(0.5)
                else:
                    raise AssertionError("cancel request did not complete")

    def test_admin_credits(self, client: httpx.Client) -> None:
        r = client.post(
            "/v1/admin/credits",
            json={"user_id": "a0000000-0000-0000-0000-000000000001", "amount": 100},
        )
        assert r.status_code == 200
        assert r.json()["new_balance"] > 0

    def test_submit_rejects_unknown_payload_fields(self, client: httpx.Client) -> None:
        r = client.post(
            "/v1/task",
            json={
                "x": 1,
                "y": 2,
                "tier": "pro",
                "model_class": "large",
            },
        )
        assert r.status_code == 422

    def test_unsupported_batch_endpoint_returns_404(self, client: httpx.Client) -> None:
        r = client.post("/v1/task/batch", json={"items": [{"x": 1, "y": 2}]})
        assert r.status_code == 404

    def test_compat_path_not_supported(self, client: httpx.Client) -> None:
        # Sol 5 intentionally exposes only /v1 paths.
        r = client.post("/task", json={"x": 1, "y": 2})
        assert r.status_code in {404, 405}

    def test_random_idempotent_payload_extra_headers_are_ignored_if_unrecognized(self, client: httpx.Client) -> None:
        key = f"sol5-scope-{uuid4()}"
        r = client.post(
            "/v1/task",
            headers={"X-Idempotency-Key": key, "X-Debug-Trace": "ignore-me"},
            json={"x": 2, "y": 3},
        )
        assert r.status_code in {200, 201}

    def test_compute_worker_exposes_metrics(self) -> None:
        compute = httpx.Client(base_url=COMPUTE_BASE, timeout=10)
        try:
            r = compute.post("/compute", json={"task_id": "metrics-task", "x": 5, "y": 6})
            assert r.status_code == 200

            metrics_response = compute.get("/metrics")
            assert metrics_response.status_code == 200
            assert "compute_requests_total" in metrics_response.text
            assert "compute_request_seconds" in metrics_response.text
        finally:
            compute.close()

    def test_prometheus_targets_include_api_and_compute(self) -> None:
        prometheus = httpx.Client(base_url=PROMETHEUS_BASE, timeout=10)
        try:
            jobs: dict[str, str] = {}
            for _ in range(30):
                try:
                    r = prometheus.get("/api/v1/targets")
                    assert r.status_code == 200

                    active_targets = r.json()["data"]["activeTargets"]
                    jobs = {target["labels"]["job"]: target["health"] for target in active_targets}
                    if jobs.get("solution5-api") == "up" and jobs.get("solution5-compute") == "up":
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.5)
            else:
                raise AssertionError(f"prometheus targets not healthy: {jobs}")
        finally:
            prometheus.close()

    def test_api_metrics_record_http_latency(self, client: httpx.Client) -> None:
        health = client.get("/health")
        assert health.status_code == 200

        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        assert "http_request_duration_seconds_count" in metrics_response.text
        assert 'endpoint="/health"' in metrics_response.text
