"""Unit tests for Settings module."""

from __future__ import annotations

from solution4.settings import Settings


class TestSettings:
    def test_defaults(self) -> None:
        settings = Settings()
        assert settings.postgres_dsn == "postgresql://postgres:postgres@localhost:5432/mc"
        assert settings.redis_url == "redis://localhost:6379/0"
        assert settings.default_task_cost == 10
        assert settings.tb_revenue_account_id == 1_000_001
        assert settings.tb_escrow_account_id == 1_000_002
        assert settings.tb_transfer_timeout_secs == 300
        assert settings.port == 8000

    def test_custom_values(self) -> None:
        settings = Settings(
            postgres_dsn="postgresql://user:pass@db:5432/mydb",
            redis_url="redis://cache:6379/1",
            default_task_cost=20,
        )
        assert settings.postgres_dsn == "postgresql://user:pass@db:5432/mydb"
        assert settings.redis_url == "redis://cache:6379/1"
        assert settings.default_task_cost == 20
