from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import PostgresDsn, RedisDsn, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DEV_ENV = "dev"
_DEV_ENV_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / ".env.dev.defaults"


class AppSettings(BaseSettings):
    """Application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        app_env = os.getenv("APP_ENV", _DEV_ENV).strip().lower()
        if app_env == _DEV_ENV and _DEV_ENV_DEFAULTS_PATH.exists():
            dev_defaults_source = DotEnvSettingsSource(
                settings_cls,
                env_file=_DEV_ENV_DEFAULTS_PATH,
                env_file_encoding="utf-8",
            )
            return (
                init_settings,
                env_settings,
                dev_defaults_source,
                file_secret_settings,
            )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    app_name: str = "mc-solution0-api"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    postgres_dsn: PostgresDsn
    redis_url: RedisDsn
    celery_broker_url: RedisDsn
    celery_result_backend: RedisDsn

    task_cost: int
    max_concurrent: int

    auth_cache_ttl_seconds: int
    idempotency_ttl_seconds: int
    task_result_ttl_seconds: int
    pending_marker_ttl_seconds: int
    orphan_marker_timeout_seconds: int = 60
    task_stuck_timeout_seconds: int = 300
    reaper_interval_seconds: int = 30
    reaper_pending_scan_count: int = 100
    reaper_pending_max_per_cycle: int = 500

    redis_retry_attempts: int = 3
    redis_retry_base_delay_seconds: float = 0.05
    redis_retry_max_delay_seconds: float = 0.5

    celery_queue_name: str = "celery"
    worker_metrics_port: int = 9100
    worker_db_timeout_seconds: float = 5.0
    worker_loop_task_timeout_seconds: float = 180.0
    worker_loop_bootstrap_timeout_seconds: float = 30.0
    worker_loop_shutdown_timeout_seconds: float = 10.0

    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_pool_command_timeout_seconds: float = 0.1
    db_statement_timeout_ms: int = 50
    db_idle_in_transaction_timeout_ms: int = 500
    db_pool_max_inactive_connection_lifetime_seconds: float = 300.0

    readiness_postgres_timeout_seconds: float = 1.0
    readiness_redis_timeout_seconds: float = 1.0
    readiness_celery_timeout_seconds: float = 1.0

    admin_api_key: str
    alice_api_key: str
    bob_api_key: str

    @model_validator(mode="after")
    def _validate_runtime_limits(self) -> AppSettings:
        if self.task_cost <= 0:
            raise ValueError("task_cost must be > 0")
        if self.max_concurrent <= 0:
            raise ValueError("max_concurrent must be > 0")
        if self.worker_db_timeout_seconds <= 0:
            raise ValueError("worker_db_timeout_seconds must be > 0")
        if self.db_pool_command_timeout_seconds <= 0:
            raise ValueError("db_pool_command_timeout_seconds must be > 0")
        if self.db_statement_timeout_ms <= 0:
            raise ValueError("db_statement_timeout_ms must be > 0")
        if self.db_idle_in_transaction_timeout_ms < 0:
            raise ValueError("db_idle_in_transaction_timeout_ms must be >= 0")
        if self.redis_socket_timeout_seconds <= 0:
            raise ValueError("redis_socket_timeout_seconds must be > 0")
        if self.redis_socket_connect_timeout_seconds <= 0:
            raise ValueError("redis_socket_connect_timeout_seconds must be > 0")
        if self.reaper_pending_scan_count < 1:
            raise ValueError("reaper_pending_scan_count must be >= 1")
        if self.reaper_pending_max_per_cycle < 1:
            raise ValueError("reaper_pending_max_per_cycle must be >= 1")
        if self.redis_retry_attempts < 1:
            raise ValueError("redis_retry_attempts must be >= 1")
        if self.redis_retry_base_delay_seconds < 0:
            raise ValueError("redis_retry_base_delay_seconds must be >= 0")
        if self.redis_retry_max_delay_seconds < 0:
            raise ValueError("redis_retry_max_delay_seconds must be >= 0")
        return self

    redis_socket_timeout_seconds: float = 0.05
    redis_socket_connect_timeout_seconds: float = 0.05


@lru_cache(maxsize=1)
def load_settings() -> AppSettings:
    """Load and cache settings for process lifetime."""

    return AppSettings()
