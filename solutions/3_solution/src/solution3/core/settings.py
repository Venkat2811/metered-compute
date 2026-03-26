from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_DEV_ENV = "dev"
_DEV_ENV_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / ".env.dev.defaults"


class AppSettings(BaseSettings):
    """Application settings loaded from environment and optional dev defaults."""

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

    app_name: str = "mc-solution3"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    postgres_dsn: PostgresDsn
    redis_url: RedisDsn
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    redpanda_bootstrap_servers: str = "redpanda:9092"
    tigerbeetle_endpoint: str = "tigerbeetle:3000"

    task_cost: int = 10
    max_concurrent_free: int = 1
    max_concurrent_pro: int = 10
    max_concurrent_enterprise: int = 64

    auth_cache_ttl_seconds: int = 60
    idempotency_ttl_seconds: int = 86_400
    task_result_ttl_seconds: int = 86_400
    pending_marker_ttl_seconds: int = 120

    hydra_public_url: str = "http://hydra:4444"
    hydra_admin_url: str = "http://hydra:4445"
    hydra_issuer: str = "http://hydra:4444/"
    hydra_jwks_url: str = "http://hydra:4444/.well-known/jwks.json"

    log_leak_sensitive_values: bool = False

    worker_loop_bootstrap_timeout_seconds: float = 3.0
    worker_loop_task_timeout_seconds: float = 60.0

    @property
    def oauth_jwks_cache_ttl_seconds(self) -> float:
        return 300.0


@lru_cache(maxsize=1)
def load_settings() -> AppSettings:
    """Load settings with a simple singleton cache for process-wide consistency."""

    return AppSettings()
