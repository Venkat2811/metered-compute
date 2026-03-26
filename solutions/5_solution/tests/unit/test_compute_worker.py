from __future__ import annotations

from fastapi.testclient import TestClient

from solution5.workers import compute_worker


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
