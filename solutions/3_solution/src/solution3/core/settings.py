from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from solution3.constants import (
    RABBITMQ_EXCHANGE_COLDSTART,
    RABBITMQ_EXCHANGE_PRELOADED,
    RABBITMQ_QUEUE_COLD,
    RABBITMQ_QUEUE_HOT_LARGE,
    RABBITMQ_QUEUE_HOT_MEDIUM,
    RABBITMQ_QUEUE_HOT_SMALL,
    REDPANDA_TOPIC_BILLING_CAPTURED,
    REDPANDA_TOPIC_BILLING_RELEASED,
    REDPANDA_TOPIC_TASK_CANCELLED,
    REDPANDA_TOPIC_TASK_COMPLETED,
    REDPANDA_TOPIC_TASK_EXPIRED,
    REDPANDA_TOPIC_TASK_FAILED,
    REDPANDA_TOPIC_TASK_REQUESTED,
    REDPANDA_TOPIC_TASK_STARTED,
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
    worker_metrics_port: int = 9100
    outbox_relay_metrics_port: int = 9200
    projector_metrics_port: int = 9300
    reconciler_metrics_port: int = 9400
    webhook_metrics_port: int = 9500
    dispatcher_metrics_port: int = 9600
    watchdog_metrics_port: int = 9700

    postgres_dsn: PostgresDsn
    redis_url: RedisDsn
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    redpanda_bootstrap_servers: str = "redpanda:9092"
    tigerbeetle_endpoint: str = "tigerbeetle:3000"
    tigerbeetle_cluster_id: int = 0
    tigerbeetle_ledger_id: int = 1
    tigerbeetle_pending_transfer_timeout_seconds: int = 600
    tigerbeetle_revenue_account_id: int = 1_000_001
    tigerbeetle_escrow_account_id: int = 1_000_002

    redpanda_topic_task_requested: str = REDPANDA_TOPIC_TASK_REQUESTED
    redpanda_topic_task_started: str = REDPANDA_TOPIC_TASK_STARTED
    redpanda_topic_task_completed: str = REDPANDA_TOPIC_TASK_COMPLETED
    redpanda_topic_task_failed: str = REDPANDA_TOPIC_TASK_FAILED
    redpanda_topic_task_cancelled: str = REDPANDA_TOPIC_TASK_CANCELLED
    redpanda_topic_task_expired: str = REDPANDA_TOPIC_TASK_EXPIRED
    redpanda_topic_billing_captured: str = REDPANDA_TOPIC_BILLING_CAPTURED
    redpanda_topic_billing_released: str = REDPANDA_TOPIC_BILLING_RELEASED

    rabbitmq_exchange_preloaded: str = RABBITMQ_EXCHANGE_PRELOADED
    rabbitmq_exchange_coldstart: str = RABBITMQ_EXCHANGE_COLDSTART
    rabbitmq_queue_hot_small: str = RABBITMQ_QUEUE_HOT_SMALL
    rabbitmq_queue_hot_medium: str = RABBITMQ_QUEUE_HOT_MEDIUM
    rabbitmq_queue_hot_large: str = RABBITMQ_QUEUE_HOT_LARGE
    rabbitmq_queue_cold: str = RABBITMQ_QUEUE_COLD

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
    oauth_request_timeout_seconds: float = 3.0

    log_leak_sensitive_values: bool = False

    worker_loop_bootstrap_timeout_seconds: float = 3.0
    worker_loop_task_timeout_seconds: float = 60.0
    dispatcher_poll_interval_seconds: float = 1.0
    projector_commit_interval_seconds: float = 1.0
    reconciler_poll_interval_seconds: float = 30.0
    billing_reconcile_stale_after_seconds: int = 720
    webhook_delivery_timeout_seconds: float = 3.0
    webhook_max_attempts: int = 3
    webhook_initial_backoff_seconds: float = 1.0
    webhook_max_backoff_seconds: float = 4.0

    admin_api_key: str
    alice_api_key: str
    bob_api_key: str

    oauth_admin_client_id: str
    oauth_admin_client_secret: str
    oauth_user1_client_id: str
    oauth_user1_client_secret: str
    oauth_user2_client_id: str
    oauth_user2_client_secret: str
    oauth_admin_tier: str
    oauth_user1_tier: str
    oauth_user2_tier: str
    oauth_admin_user_id: UUID
    oauth_user1_user_id: UUID
    oauth_user2_user_id: UUID

    @property
    def oauth_jwks_cache_ttl_seconds(self) -> float:
        return 300.0


@lru_cache(maxsize=1)
def load_settings() -> AppSettings:
    """Load settings with a simple singleton cache for process-wide consistency."""

    return AppSettings()
