from __future__ import annotations

import pytest

from solution3.services import webhook_security


def test_validate_callback_url_format_rejects_localhost_literal_ip_and_credentials() -> None:
    with pytest.raises(ValueError, match="host is not allowed"):
        webhook_security.validate_callback_url_format(
            "http://127.0.0.1:8080/callback",
            app_env="prod",
        )

    with pytest.raises(ValueError, match="credentials"):
        webhook_security.validate_callback_url_format(
            "https://user:pass@example.test/callback",
            app_env="prod",
        )


def test_validate_callback_url_format_allows_host_docker_internal_only_in_dev_like_envs() -> None:
    webhook_security.validate_callback_url_format(
        "http://host.docker.internal:8080/callback",
        app_env="test",
    )

    with pytest.raises(ValueError, match="host is not allowed"):
        webhook_security.validate_callback_url_format(
            "http://host.docker.internal:8080/callback",
            app_env="prod",
        )


@pytest.mark.asyncio
async def test_ensure_callback_url_delivery_safe_rejects_private_dns_resolution() -> None:
    async def fake_resolve(*, hostname: str, port: int | None) -> list[str]:
        assert hostname == "internal.example.test"
        assert port == 8443
        return ["10.1.2.3"]

    with pytest.raises(ValueError, match="host resolves to a private or reserved address"):
        await webhook_security.ensure_callback_url_delivery_safe(
            "https://internal.example.test:8443/callback",
            app_env="prod",
            resolve_host=fake_resolve,
        )


@pytest.mark.asyncio
async def test_ensure_callback_url_delivery_safe_allows_public_resolution() -> None:
    async def fake_resolve(*, hostname: str, port: int | None) -> list[str]:
        assert hostname == "example.test"
        assert port == 443
        return ["93.184.216.34"]

    await webhook_security.ensure_callback_url_delivery_safe(
        "https://example.test:443/callback",
        app_env="prod",
        resolve_host=fake_resolve,
    )
