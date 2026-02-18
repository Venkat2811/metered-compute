from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from solution0.core.settings import AppSettings, load_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    load_settings.cache_clear()


def test_load_settings_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://postgres:postgres@postgres:5432/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis:6379/1")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://redis:6379/2")

    settings = load_settings()

    assert str(settings.postgres_dsn) == "postgresql://postgres:postgres@postgres:5432/postgres"
    assert str(settings.redis_url) == "redis://redis:6379/0"


def test_settings_requires_mandatory_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "")

    with pytest.raises(ValidationError):
        AppSettings()


def _base_settings_kwargs() -> dict[str, Any]:
    return {
        "postgres_dsn": "postgresql://postgres:postgres@postgres:5432/postgres",
        "redis_url": "redis://redis:6379/0",
        "celery_broker_url": "redis://redis:6379/1",
        "celery_result_backend": "redis://redis:6379/2",
        "task_cost": 10,
        "max_concurrent": 3,
        "auth_cache_ttl_seconds": 60,
        "idempotency_ttl_seconds": 86400,
        "task_result_ttl_seconds": 86400,
        "pending_marker_ttl_seconds": 120,
        "admin_api_key": "e1138140-6c35-49b6-b723-ba8d609d8eb5",
        "alice_api_key": "586f0ef6-e655-4413-ab08-a481db150389",
        "bob_api_key": "c9169bc2-2980-4155-be29-442ffc44ce64",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("db_pool_command_timeout_seconds", 0),
        ("db_statement_timeout_ms", 0),
        ("db_idle_in_transaction_timeout_ms", -1),
        ("redis_socket_timeout_seconds", 0),
        ("redis_socket_connect_timeout_seconds", 0),
    ],
)
def test_settings_rejects_invalid_runtime_timeouts(field: str, value: object) -> None:
    kwargs = _base_settings_kwargs()
    kwargs[field] = value
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_rejects_non_positive_task_cost() -> None:
    with pytest.raises(ValidationError):
        AppSettings(task_cost=0)


def test_settings_rejects_non_positive_max_concurrent() -> None:
    with pytest.raises(ValidationError):
        AppSettings(max_concurrent=0)
