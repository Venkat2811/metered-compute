from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from solution2.services import rabbitmq as rabbitmq_service


@dataclass
class _FakeParameters:
    url: str
    heartbeat: int | None = None
    blocked_connection_timeout: float | None = None
    socket_timeout: float | None = None


class _FakeChannel:
    def __init__(self) -> None:
        self.exchanges: list[tuple[str, str]] = []
        self.queues: list[tuple[str, dict[str, object] | None]] = []
        self.bindings: list[tuple[str, str, str]] = []
        self.published: list[tuple[str, str, str, str | None]] = []
        self.closed = False

    def confirm_delivery(self) -> None:
        return None

    def exchange_declare(self, *, exchange: str, exchange_type: str, durable: bool) -> None:
        self.exchanges.append((exchange, exchange_type))

    def queue_declare(
        self,
        *,
        queue: str,
        durable: bool,
        arguments: dict[str, object] | None,
    ) -> None:
        self.queues.append((queue, arguments))

    def queue_bind(self, *, exchange: str, queue: str, routing_key: str) -> None:
        self.bindings.append((exchange, queue, routing_key))

    def basic_publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: str,
        properties: Any,
        mandatory: bool,
    ) -> bool:
        self.published.append(
            (exchange, routing_key, body, getattr(properties, "message_id", None))
        )
        return True

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel
        self.closed = False

    def channel(self) -> _FakeChannel:
        return self._channel

    def close(self) -> None:
        self.closed = True


def test_rabbitmq_connect_initializes_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _FakeChannel()
    captured: dict[str, float | str | None] = {}
    connect_calls = {"count": 0}

    def fake_url_parameters(url: str) -> _FakeParameters:
        captured["url"] = url
        return _FakeParameters(url=url)

    def fake_connection_factory(*, parameters: _FakeParameters) -> _FakeConnection:
        connect_calls["count"] += 1
        captured["heartbeat"] = parameters.heartbeat
        captured["blocked_connection_timeout"] = parameters.blocked_connection_timeout
        captured["socket_timeout"] = parameters.socket_timeout
        return _FakeConnection(channel=channel)

    monkeypatch.setattr(
        rabbitmq_service,
        "pika",
        SimpleNamespace(
            URLParameters=fake_url_parameters,
            BlockingConnection=fake_connection_factory,
            BasicProperties=object,
        ),
    )

    relay = rabbitmq_service.RabbitMQRelay(
        rabbitmq_url="amqp://guest:guest@rabbitmq:5672/",
        socket_connect_timeout=4.5,
    )
    relay.connect()
    relay.connect()

    assert connect_calls["count"] == 1
    assert captured["url"] == "amqp://guest:guest@rabbitmq:5672/"
    assert captured["heartbeat"] == 60
    assert captured["blocked_connection_timeout"] == 4.5
    assert captured["socket_timeout"] == 4.5
    assert relay._connection is not None
    assert relay._channel is channel


def test_rabbitmq_ensure_topology_declares_expected_bindings() -> None:
    channel = _FakeChannel()
    relay = rabbitmq_service.RabbitMQRelay(rabbitmq_url="amqp://guest:guest@rabbitmq:5672/")
    relay._connection = _FakeConnection(channel=channel)
    relay._channel = channel

    relay.ensure_topology()
    relay.ensure_topology()

    exchanges = {name for name, _ in channel.exchanges}
    assert rabbitmq_service.OUTBOX_EXCHANGE_NAME in exchanges
    assert rabbitmq_service.OUTBOX_DLX_NAME in exchanges
    queue_names = {name for name, _ in channel.queues}
    assert "queue.realtime" in queue_names
    assert "queue.fast" in queue_names
    assert "queue.batch" in queue_names
    assert "queue.projector" in queue_names
    assert "webhook" in queue_names
    bindings = {(queue, key) for _, queue, key in channel.bindings}
    assert ("webhook", "webhook") in bindings


def test_rabbitmq_publish_marks_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _FakeChannel()

    def rejecting_publish(**_: object) -> bool:
        return False

    channel.basic_publish = rejecting_publish  # type: ignore[method-assign]

    relay = rabbitmq_service.RabbitMQRelay(rabbitmq_url="amqp://guest:guest@rabbitmq:5672/")
    relay._connection = _FakeConnection(channel=channel)
    relay._channel = channel

    with pytest.raises(rabbitmq_service.RabbitMQRelayError):
        relay.publish(
            event_id="event-1",
            routing_key="tasks.batch.free.small",
            payload={"event_id": "event-1"},
        )


def test_rabbitmq_connect_without_dependency_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rabbitmq_service, "pika", None)
    relay = rabbitmq_service.RabbitMQRelay(rabbitmq_url="amqp://guest:guest@rabbitmq:5672/")

    with pytest.raises(rabbitmq_service.RabbitMQRelayError, match="pika is not installed"):
        relay.connect()
