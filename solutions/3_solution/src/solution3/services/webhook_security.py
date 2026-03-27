from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

_DEV_ALLOWED_HOSTS = frozenset({"host.docker.internal", "gateway.docker.internal"})
_BLOCKED_HOSTS = frozenset({"localhost", "localhost.localdomain"})
_DEV_LIKE_ENVS = frozenset({"dev", "test"})

ResolveHost = Callable[..., Awaitable[list[str]]]


def _is_dev_allowed_host(*, hostname: str, app_env: str) -> bool:
    return hostname in _DEV_ALLOWED_HOSTS and app_env.strip().lower() in _DEV_LIKE_ENVS


def _is_blocked_ip(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_reserved
        or value.is_unspecified
    )


def validate_callback_url_format(callback_url: str, *, app_env: str) -> None:
    parsed = urlsplit(callback_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("callback_url must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("callback_url cannot include credentials")
    if not parsed.hostname:
        raise ValueError("callback_url must include a host")

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in _BLOCKED_HOSTS:
        raise ValueError("callback_url host is not allowed")
    if hostname in _DEV_ALLOWED_HOSTS:
        if _is_dev_allowed_host(hostname=hostname, app_env=app_env):
            return
        raise ValueError("callback_url host is not allowed")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if _is_blocked_ip(literal_ip):
        raise ValueError("callback_url host is not allowed")


async def _resolve_host_with_system_dns(*, hostname: str, port: int | None) -> list[str]:
    loop = asyncio.get_running_loop()
    addrinfo = await loop.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    resolved: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in addrinfo:
        candidate = sockaddr[0]
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved


async def ensure_callback_url_delivery_safe(
    callback_url: str,
    *,
    app_env: str,
    resolve_host: ResolveHost | None = None,
) -> None:
    validate_callback_url_format(callback_url, app_env=app_env)
    parsed = urlsplit(callback_url)
    if parsed.hostname is None:
        raise ValueError("callback_url must include a host")
    hostname = parsed.hostname.lower().rstrip(".")
    if _is_dev_allowed_host(hostname=hostname, app_env=app_env):
        return

    try:
        ipaddress.ip_address(hostname)
        return
    except ValueError:
        pass

    resolver = resolve_host or _resolve_host_with_system_dns
    resolved_ips = await resolver(hostname=hostname, port=parsed.port or None)
    if not resolved_ips:
        raise ValueError("callback_url host could not be resolved")
    for resolved_ip in resolved_ips:
        if _is_blocked_ip(ipaddress.ip_address(resolved_ip)):
            raise ValueError("callback_url host resolves to a private or reserved address")
