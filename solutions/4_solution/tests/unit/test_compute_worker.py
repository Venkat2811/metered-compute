from __future__ import annotations

from fastapi.testclient import TestClient

from solution4 import metrics
from solution4.workers import compute_worker


def test_compute_worker_health_and_ready() -> None:
    client = TestClient(compute_worker.create_app())

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ok"}


def test_compute_endpoint_returns_result() -> None:
    client = TestClient(compute_worker.create_app())

    response = client.post("/compute", json={"task_id": "task-1", "x": 2, "y": 3})

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == "task-1"
    assert payload["result"] == {"sum": 5, "product": 6}


def test_compute_endpoint_rejects_empty_task_id() -> None:
    client = TestClient(compute_worker.create_app())

    response = client.post("/compute", json={"task_id": "", "x": 1, "y": 2})

    assert response.status_code == 422


def test_compute_endpoint_is_idempotent() -> None:
    app = compute_worker.create_app()
    client = TestClient(app)

    compute_worker._cache.clear()

    response_a = client.post("/compute", json={"task_id": "task-idempotent", "x": 1, "y": 2})
    response_b = client.post("/compute", json={"task_id": "task-idempotent", "x": 1, "y": 2})

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json() == response_b.json()


def test_compute_worker_exposes_metrics_and_records_requests() -> None:
    client = TestClient(compute_worker.create_app())
    histogram = next(iter(metrics.COMPUTE_LATENCY_SECONDS.collect()))

    before_ok = metrics.COMPUTE_REQUESTS.labels(result="ok")._value.get()
    before_latency = next(
        sample.value for sample in histogram.samples if sample.name == "compute_request_seconds_count"
    )

    response = client.post("/compute", json={"task_id": "task-metrics", "x": 7, "y": 8})
    assert response.status_code == 200

    metrics_response = client.get("/metrics")

    assert metrics_response.status_code == 200
    assert "compute_requests_total" in metrics_response.text
    assert "compute_request_seconds" in metrics_response.text
    assert metrics.COMPUTE_REQUESTS.labels(result="ok")._value.get() == before_ok + 1
    histogram = next(iter(metrics.COMPUTE_LATENCY_SECONDS.collect()))
    after_latency = next(sample.value for sample in histogram.samples if sample.name == "compute_request_seconds_count")
    assert after_latency == before_latency + 1
