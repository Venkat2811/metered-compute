from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import PostgresDsn, RedisDsn, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from solution1.constants import (
    SubscriptionTier,
    minimum_stream_claim_idle_ms,
    minimum_worker_heartbeat_ttl_seconds,
)

_DEV_ENV = "dev"
_DEV_ENV_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / ".env.dev.defaults"
_NON_PRODUCTION_API_KEY_PLACEHOLDERS: set[str] = {
    "e1138140-6c35-49b6-b723-ba8d609d8eb5",
    "586f0ef6-e655-4413-ab08-a481db150389",
    "c9169bc2-2980-4155-be29-442ffc44ce64",
}
_NON_PRODUCTION_CLIENT_SECRET_PLACEHOLDERS: set[str] = {
    "solution1-admin-secret",
    "solution1-user1-secret",
    "solution1-user2-secret",
}
_MIN_CLIENT_SECRET_CHARS = 24


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

    @classmethod
    @model_validator(mode="before")
    def _load_secret_files(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        file_backed_fields = {
            "ADMIN_API_KEY": ("admin_api_key", _NON_PRODUCTION_API_KEY_PLACEHOLDERS),
            "ALICE_API_KEY": (
                "alice_api_key",
                _NON_PRODUCTION_API_KEY_PLACEHOLDERS,
            ),
            "BOB_API_KEY": (
                "bob_api_key",
                _NON_PRODUCTION_API_KEY_PLACEHOLDERS,
            ),
            "OAUTH_ADMIN_CLIENT_SECRET": (
                "oauth_admin_client_secret",
                _NON_PRODUCTION_CLIENT_SECRET_PLACEHOLDERS,
            ),
            "OAUTH_USER1_CLIENT_SECRET": (
                "oauth_user1_client_secret",
                _NON_PRODUCTION_CLIENT_SECRET_PLACEHOLDERS,
            ),
            "OAUTH_USER2_CLIENT_SECRET": (
                "oauth_user2_client_secret",
                _NON_PRODUCTION_CLIENT_SECRET_PLACEHOLDERS,
            ),
        }

        for env_name, (field_name, invalid_placeholders) in file_backed_fields.items():
            file_env_name = f"{env_name}_FILE"
            file_path = values.get(file_env_name)
            if not isinstance(file_path, str):
                file_path = os.getenv(file_env_name)
            if not file_path:
                continue

            value = values.get(field_name, "").strip() if values.get(field_name) else ""
            if value and value not in invalid_placeholders:
                continue

            try:
                values[field_name] = Path(file_path).read_text().strip()
            except OSError as exc:
                raise ValueError(
                    f"{env_name} not provided and {file_env_name} is unreadable"
                ) from exc

        return values

    app_name: str = "mc-solution1-api"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    postgres_dsn: PostgresDsn
    redis_url: RedisDsn

    task_cost: int
    max_concurrent: int

    auth_cache_ttl_seconds: int
    idempotency_ttl_seconds: int
    task_result_ttl_seconds: int
    pending_marker_ttl_seconds: int
    orphan_marker_timeout_seconds: int = 60
    task_stuck_timeout_seconds: int = 300
    reaper_interval_seconds: int = 30
    reaper_error_backoff_seconds: float = 1.0
    reaper_pending_scan_count: int = 100
    reaper_pending_max_per_cycle: int = 500
    reaper_retention_batch_size: int = 500
    reaper_credit_transaction_retention_seconds: int = 86_400
    reaper_credit_drift_audit_retention_seconds: int = 86_400

    redis_retry_attempts: int = 3
    redis_retry_base_delay_seconds: float = 0.05
    redis_retry_max_delay_seconds: float = 0.5

    redis_tasks_stream_key: str = "tasks:stream"
    redis_tasks_stream_maxlen: int = 500_000
    redis_tasks_stream_group: str = "workers"
    redis_task_state_ttl_seconds: int = 86_400
    stream_worker_read_count: int = 1
    stream_worker_block_ms: int = 5_000
    stream_worker_claim_idle_ms: int = 15_000
    stream_worker_claim_count: int = 20
    stream_worker_heartbeat_key: str = "workers:stream:last_seen"
    stream_worker_heartbeat_ttl_seconds: int = 30
    stream_worker_error_backoff_seconds: float = 1.0
    hydra_public_url: str = "http://hydra:4444"
    hydra_admin_url: str = "http://hydra:4445"
    hydra_issuer: str = "http://hydra:4444/"
    hydra_jwks_url: str = "http://hydra:4444/.well-known/jwks.json"
    hydra_jwks_cache_ttl_seconds: float = 300.0
    hydra_expected_audience: str | None = None
    otel_enabled: bool = False
    otel_service_namespace: str = "metered-compute"
    otel_exporter_otlp_traces_endpoint: str = "http://otel-collector:4318/v1/traces"
    otel_export_timeout_seconds: float = 3.0
    otel_sampler_ratio: float = 1.0
    oauth_default_scope: str = "task:submit task:poll task:cancel"
    oauth_request_timeout_seconds: float = 3.0
    oauth_token_rate_limit_enabled: bool = True
    oauth_token_rate_limit_window_seconds: int = 60
    oauth_token_rate_limit_max_requests: int = 120
    oauth_admin_client_id: str = "solution1-admin"
    oauth_admin_client_secret: str = "solution1-admin-secret"
    oauth_user1_client_id: str = "solution1-user1"
    oauth_user1_client_secret: str = "solution1-user1-secret"
    oauth_user2_client_id: str = "solution1-user2"
    oauth_user2_client_secret: str = "solution1-user2-secret"
    oauth_admin_tier: SubscriptionTier = SubscriptionTier.ENTERPRISE
    oauth_user1_tier: SubscriptionTier = SubscriptionTier.PRO
    oauth_user2_tier: SubscriptionTier = SubscriptionTier.FREE
    oauth_admin_user_id: UUID
    oauth_user1_user_id: UUID
    oauth_user2_user_id: UUID
    revocation_bucket_ttl_seconds: int = 129_600
    worker_metrics_port: int = 9100
    reaper_metrics_port: int = 9201
    webhook_enabled: bool = True
    webhook_queue_key: str = "webhook:queue"
    webhook_queue_maxlen: int = 100_000
    webhook_scheduled_key: str = "webhook:scheduled"
    webhook_dlq_key: str = "webhook:dlq"
    webhook_dispatch_batch_size: int = 100
    webhook_dispatcher_poll_timeout_seconds: int = 2
    webhook_delivery_timeout_seconds: float = 3.0
    webhook_dispatch_error_backoff_seconds: float = 1.0
    webhook_max_attempts: int = 5
    webhook_initial_backoff_seconds: float = 1.0
    webhook_backoff_multiplier: float = 2.0
    webhook_max_backoff_seconds: float = 60.0
    webhook_metrics_port: int = 9300
    redis_socket_timeout_seconds: float = 0.05
    redis_socket_connect_timeout_seconds: float = 0.05
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
    readiness_worker_timeout_seconds: float = 1.0

    admin_api_key: str
    alice_api_key: str
    bob_api_key: str

    @model_validator(mode="after")
    def _validate_worker_runtime_windows(self) -> AppSettings:
        min_claim_idle_ms = minimum_stream_claim_idle_ms()
        if self.stream_worker_claim_idle_ms < min_claim_idle_ms:
            raise ValueError(
                "stream_worker_claim_idle_ms must be >= "
                f"{min_claim_idle_ms} to avoid premature reclaim"
            )

        min_heartbeat_ttl_seconds = minimum_worker_heartbeat_ttl_seconds(
            block_ms=self.stream_worker_block_ms
        )
        if self.stream_worker_heartbeat_ttl_seconds < min_heartbeat_ttl_seconds:
            raise ValueError(
                "stream_worker_heartbeat_ttl_seconds must be >= "
                f"{min_heartbeat_ttl_seconds} for the configured block/runtime window"
            )

        app_env = self.app_env.strip().lower()
        if app_env != _DEV_ENV:
            self._validate_non_dev_secret_hygiene()
        self._validate_oauth_rate_limit_settings()
        self._validate_reaper_retention_settings()
        self._validate_runtime_limits()
        self._validate_webhook_settings()

        return self

    def _validate_oauth_rate_limit_settings(self) -> None:
        if self.oauth_token_rate_limit_window_seconds < 1:
            raise ValueError("oauth_token_rate_limit_window_seconds must be >= 1")
        if self.oauth_token_rate_limit_max_requests < 1:
            raise ValueError("oauth_token_rate_limit_max_requests must be >= 1")

    def _validate_reaper_retention_settings(self) -> None:
        if self.reaper_retention_batch_size < 1:
            raise ValueError("reaper_retention_batch_size must be >= 1")

        for name in (
            "reaper_credit_transaction_retention_seconds",
            "reaper_credit_drift_audit_retention_seconds",
        ):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be >= 0")

    def _validate_runtime_limits(self) -> None:
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

    def _validate_webhook_settings(self) -> None:
        if self.webhook_queue_maxlen < 1:
            raise ValueError("webhook_queue_maxlen must be >= 1")
        if self.webhook_dispatch_batch_size < 1:
            raise ValueError("webhook_dispatch_batch_size must be >= 1")
        if self.webhook_dispatcher_poll_timeout_seconds < 1:
            raise ValueError("webhook_dispatcher_poll_timeout_seconds must be >= 1")
        if self.webhook_delivery_timeout_seconds <= 0:
            raise ValueError("webhook_delivery_timeout_seconds must be > 0")
        if self.webhook_dispatch_error_backoff_seconds <= 0:
            raise ValueError("webhook_dispatch_error_backoff_seconds must be > 0")
        if self.webhook_max_attempts < 1:
            raise ValueError("webhook_max_attempts must be >= 1")
        if self.webhook_initial_backoff_seconds <= 0:
            raise ValueError("webhook_initial_backoff_seconds must be > 0")
        if self.webhook_backoff_multiplier < 1.0:
            raise ValueError("webhook_backoff_multiplier must be >= 1.0")
        if self.webhook_max_backoff_seconds <= 0:
            raise ValueError("webhook_max_backoff_seconds must be > 0")

    def _validate_non_dev_secret_hygiene(self) -> None:
        for field in ("admin_api_key", "alice_api_key", "bob_api_key"):
            value = getattr(self, field)
            self._require_uuid_setting(field, value)
            if value in _NON_PRODUCTION_API_KEY_PLACEHOLDERS:
                raise ValueError(f"{field} cannot use default dev placeholder")

        for name, value in (
            ("oauth_admin_client_secret", self.oauth_admin_client_secret),
            ("oauth_user1_client_secret", self.oauth_user1_client_secret),
            ("oauth_user2_client_secret", self.oauth_user2_client_secret),
        ):
            self._require_strong_secret(
                name,
                value,
                placeholders=_NON_PRODUCTION_CLIENT_SECRET_PLACEHOLDERS,
            )

        if self.hydra_jwks_cache_ttl_seconds < 0:
            raise ValueError("hydra_jwks_cache_ttl_seconds must be >= 0")
        if self.otel_export_timeout_seconds <= 0:
            raise ValueError("otel_export_timeout_seconds must be > 0")
        if not (0.0 <= self.otel_sampler_ratio <= 1.0):
            raise ValueError("otel_sampler_ratio must be between 0.0 and 1.0")

    @staticmethod
    def _require_uuid_setting(name: str, value: str) -> None:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a valid UUID string") from exc

    @staticmethod
    def _require_strong_secret(
        name: str,
        value: str,
        *,
        placeholders: set[str],
    ) -> None:
        normalized = value.strip()
        if normalized in placeholders:
            raise ValueError(f"{name} cannot use default dev placeholder secret")
        if len(normalized) < _MIN_CLIENT_SECRET_CHARS:
            raise ValueError(f"{name} must be at least {_MIN_CLIENT_SECRET_CHARS} characters long")


@lru_cache(maxsize=1)
def load_settings() -> AppSettings:
    """Load and cache settings for process lifetime."""

    return AppSettings()
