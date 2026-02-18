from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from solution2.core.settings import AppSettings, load_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    load_settings.cache_clear()


def test_load_settings_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://postgres:postgres@postgres:5432/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")

    settings = load_settings()

    assert str(settings.postgres_dsn) == "postgresql://postgres:postgres@postgres:5432/postgres"
    assert str(settings.redis_url) == "redis://redis:6379/0"


def test_settings_requires_mandatory_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "")
    monkeypatch.setenv("REDIS_URL", "")

    with pytest.raises(ValidationError):
        AppSettings()


def test_settings_rejects_non_positive_worker_heartbeat_ttl() -> None:
    with pytest.raises(ValidationError):
        AppSettings(worker_heartbeat_ttl_seconds=0)


def test_settings_rejects_non_positive_task_cost() -> None:
    with pytest.raises(ValidationError):
        AppSettings(task_cost=0)


def test_settings_rejects_non_positive_max_concurrent() -> None:
    with pytest.raises(ValidationError):
        AppSettings(max_concurrent=0)


def test_settings_rejects_invalid_webhook_queue_maxlen() -> None:
    with pytest.raises(ValidationError):
        AppSettings(webhook_queue_maxlen=0)


def _non_dev_settings_kwargs(**overrides: object) -> dict[str, Any]:
    base_kwargs: dict[str, Any] = {
        "app_env": "prod",
        "postgres_dsn": "postgresql://postgres:postgres@postgres:5432/postgres",
        "redis_url": "redis://redis:6379/0",
        "task_cost": 10,
        "max_concurrent": 3,
        "auth_cache_ttl_seconds": 60,
        "idempotency_ttl_seconds": 3600,
        "task_result_ttl_seconds": 86400,
        "pending_marker_ttl_seconds": 120,
        "admin_api_key": str(uuid4()),
        "alice_api_key": str(uuid4()),
        "bob_api_key": str(uuid4()),
        "oauth_admin_user_id": str(uuid4()),
        "oauth_user1_user_id": str(uuid4()),
        "oauth_user2_user_id": str(uuid4()),
        "oauth_admin_client_secret": "a" * 24,
        "oauth_user1_client_secret": "b" * 24,
        "oauth_user2_client_secret": "c" * 24,
    }
    base_kwargs.update(overrides)
    return base_kwargs


@pytest.mark.parametrize(
    "field,value",
    [
        ("admin_api_key", "e1138140-6c35-49b6-b723-ba8d609d8eb5"),
        ("alice_api_key", "586f0ef6-e655-4413-ab08-a481db150389"),
        ("bob_api_key", "c9169bc2-2980-4155-be29-442ffc44ce64"),
        ("oauth_admin_client_secret", "solution2-admin-secret"),
        ("oauth_user1_client_secret", "solution2-user1-secret"),
        ("oauth_user2_client_secret", "solution2-user2-secret"),
    ],
)
def test_settings_non_dev_rejects_placeholder_secrets_and_api_keys(field: str, value: str) -> None:
    kwargs = _non_dev_settings_kwargs()
    kwargs[field] = value
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_short_client_secret() -> None:
    kwargs = _non_dev_settings_kwargs(oauth_admin_client_secret="short")
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_invalid_api_key_uuid() -> None:
    kwargs = _non_dev_settings_kwargs(admin_api_key="not-a-uuid")
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_negative_jwks_cache_ttl() -> None:
    kwargs = _non_dev_settings_kwargs(hydra_jwks_cache_ttl_seconds=-1)
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_invalid_otel_sampler_ratio() -> None:
    kwargs = _non_dev_settings_kwargs(otel_sampler_ratio=1.2)
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_invalid_watchdog_interval() -> None:
    kwargs = _non_dev_settings_kwargs(watchdog_interval_seconds=0)
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_invalid_webhook_attempts() -> None:
    kwargs = _non_dev_settings_kwargs(webhook_max_attempts=0)
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_rejects_invalid_watchdog_scan_count() -> None:
    kwargs = _non_dev_settings_kwargs(watchdog_scan_count=0)
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)


def test_settings_non_dev_supports_direct_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_api_key = str(uuid4())
    alice_api_key = str(uuid4())
    bob_api_key = str(uuid4())
    admin_secret = "a" * 24
    user1_secret = "b" * 24
    user2_secret = "c" * 24

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://postgres:postgres@postgres:5432/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("TASK_COST", "10")
    monkeypatch.setenv("MAX_CONCURRENT", "3")
    monkeypatch.setenv("AUTH_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("IDEMPOTENCY_TTL_SECONDS", "3600")
    monkeypatch.setenv("TASK_RESULT_TTL_SECONDS", "86400")
    monkeypatch.setenv("PENDING_MARKER_TTL_SECONDS", "120")
    monkeypatch.setenv("OAUTH_ADMIN_USER_ID", str(uuid4()))
    monkeypatch.setenv("OAUTH_USER1_USER_ID", str(uuid4()))
    monkeypatch.setenv("OAUTH_USER2_USER_ID", str(uuid4()))
    monkeypatch.setenv("ADMIN_API_KEY", admin_api_key)
    monkeypatch.setenv("ALICE_API_KEY", alice_api_key)
    monkeypatch.setenv("BOB_API_KEY", bob_api_key)
    monkeypatch.setenv("OAUTH_ADMIN_CLIENT_SECRET", admin_secret)
    monkeypatch.setenv("OAUTH_USER1_CLIENT_SECRET", user1_secret)
    monkeypatch.setenv("OAUTH_USER2_CLIENT_SECRET", user2_secret)

    settings = AppSettings()
    assert settings.admin_api_key == admin_api_key
    assert settings.alice_api_key == alice_api_key
    assert settings.bob_api_key == bob_api_key
    assert settings.oauth_admin_client_secret == admin_secret
    assert settings.oauth_user1_client_secret == user1_secret
    assert settings.oauth_user2_client_secret == user2_secret


def test_settings_default_timeouts() -> None:
    settings = load_settings()
    assert settings.db_pool_command_timeout_seconds == 0.1
    assert settings.db_statement_timeout_ms == 50
    assert settings.db_statement_timeout_batch_ms == 2_000
    assert settings.db_idle_in_transaction_timeout_ms == 500
    assert settings.redis_socket_timeout_seconds == 0.05
    assert settings.redis_socket_connect_timeout_seconds == 0.05
    assert settings.outbox_relay_batch_size == 100
    assert settings.outbox_relay_empty_backoff_seconds == 0.05
    assert settings.outbox_relay_error_backoff_seconds == 1.0
    assert settings.outbox_relay_connect_timeout_seconds == 3.0
    assert settings.outbox_relay_purge_interval_seconds == 60.0
    assert settings.outbox_relay_purge_retention_seconds == 604_800
    assert settings.outbox_relay_purge_batch_size == 500
    assert settings.outbox_relay_metrics_port == 9200
    assert settings.watchdog_interval_seconds == 30.0
    assert settings.watchdog_scan_count == 100
    assert settings.worker_heartbeat_ttl_seconds == 30


@pytest.mark.parametrize(
    "field,value",
    [
        ("db_pool_command_timeout_seconds", 0.0),
        ("db_statement_timeout_ms", 0),
        ("db_statement_timeout_batch_ms", 0),
        ("db_idle_in_transaction_timeout_ms", 0),
        ("redis_socket_timeout_seconds", 0.0),
        ("redis_socket_connect_timeout_seconds", 0.0),
        ("outbox_relay_batch_size", 0),
        ("outbox_relay_empty_backoff_seconds", 0.0),
        ("outbox_relay_error_backoff_seconds", 0.0),
        ("outbox_relay_connect_timeout_seconds", 0.0),
        ("outbox_relay_purge_interval_seconds", 0.0),
        ("outbox_relay_purge_retention_seconds", 0),
        ("outbox_relay_purge_batch_size", 0),
        ("outbox_relay_metrics_port", 0),
    ],
)
def test_settings_rejects_invalid_timeout_values(field: str, value: float | int) -> None:
    kwargs = _non_dev_settings_kwargs()
    kwargs[field] = value
    with pytest.raises(ValidationError):
        AppSettings(**kwargs)
