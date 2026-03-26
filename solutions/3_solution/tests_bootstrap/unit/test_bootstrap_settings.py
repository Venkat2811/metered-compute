from __future__ import annotations

from fastapi.testclient import TestClient

from solution3.app import create_app
from solution3.core.settings import load_settings


def test_load_settings_uses_dev_defaults() -> None:
    settings = load_settings()

    assert settings.app_name == "mc-solution3"
    assert str(settings.postgres_dsn) == "postgresql://postgres:postgres@postgres:5432/postgres"
    assert str(settings.redis_url) == "redis://redis:6379/0"
    assert settings.redpanda_bootstrap_servers == "redpanda:9092"
    assert settings.tigerbeetle_endpoint == "tigerbeetle:3000"
    assert settings.max_concurrent_free == 1
    assert settings.max_concurrent_pro == 10
    assert settings.max_concurrent_enterprise == 64


def test_create_app_exposes_health_and_ready_routes() -> None:
    with TestClient(create_app()) as client:
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json()["solution"] == "3_solution"
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert "redpanda" in ready.json()["dependencies"]
    assert "tigerbeetle" in ready.json()["dependencies"]
