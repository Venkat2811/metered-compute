from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Postgres
    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/mc"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # TigerBeetle
    tigerbeetle_addresses: str = "localhost:3000"
    tigerbeetle_cluster_id: int = 0

    # Restate
    restate_ingress_url: str = "http://localhost:8080"
    restate_admin_url: str = "http://localhost:9070"

    # Compute worker
    compute_worker_url: str = "http://localhost:8001"
    compute_timeout_seconds: float = 2.0
    compute_retry_attempts: int = 1

    # API
    host: str = "0.0.0.0"
    port: int = 8000

    # Task defaults
    default_task_cost: int = 10
    tb_transfer_timeout_secs: int = 300

    # Platform TigerBeetle account IDs (fixed)
    tb_revenue_account_id: int = 1_000_001
    tb_escrow_account_id: int = 1_000_002

    model_config = {"env_prefix": "", "case_sensitive": False}
