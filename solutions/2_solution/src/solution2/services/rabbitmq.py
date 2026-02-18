"""RabbitMQ publisher helpers for the outbox relay."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from solution2.utils.logging import get_logger

try:
    import pika  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - exercised in dependency-injected tests
    pika = None

logger = get_logger("solution2.services.rabbitmq")

OUTBOX_EXCHANGE_NAME = "exchange.tasks"
OUTBOX_DLX_NAME = "exchange.tasks.dlx"
OUTBOX_TOPICS = ("tasks.realtime", "tasks.fast", "tasks.batch")
OUTBOX_QUEUES = (
    "queue.realtime",
    "queue.fast",
    "queue.batch",
    "queue.projector",
    "queue.audit",
    "webhook",
)


@dataclass(frozen=True)
class QueueBinding:
    """Binding descriptors for outbox queue setup."""

    queue: str
    routing_pattern: str
    dlq_queue: str


def _default_bindings() -> tuple[QueueBinding, ...]:
    # Route request-mode+tier keys into SLA lane queues.
    base_bindings = (
        QueueBinding(
            queue="queue.batch",
            routing_pattern="tasks.async.free.*",
            dlq_queue="queue.batch.dlq",
        ),
        QueueBinding(
            queue="queue.batch",
            routing_pattern="tasks.batch.free.*",
            dlq_queue="queue.batch.dlq",
        ),
        QueueBinding(
            queue="queue.batch",
            routing_pattern="tasks.batch.pro.*",
            dlq_queue="queue.batch.dlq",
        ),
        QueueBinding(
            queue="queue.fast",
            routing_pattern="tasks.async.pro.*",
            dlq_queue="queue.fast.dlq",
        ),
        QueueBinding(
            queue="queue.fast",
            routing_pattern="tasks.sync.pro.*",
            dlq_queue="queue.fast.dlq",
        ),
        QueueBinding(
            queue="queue.fast",
            routing_pattern="tasks.batch.enterprise.*",
            dlq_queue="queue.fast.dlq",
        ),
        QueueBinding(
            queue="queue.realtime",
            routing_pattern="tasks.async.enterprise.*",
            dlq_queue="queue.realtime.dlq",
        ),
        QueueBinding(
            queue="queue.realtime",
            routing_pattern="tasks.sync.enterprise.*",
            dlq_queue="queue.realtime.dlq",
        ),
        QueueBinding(
            queue="queue.audit",
            routing_pattern="admin.#",
            dlq_queue="queue.audit.dlq",
        ),
    )
    return (
        *base_bindings,
        QueueBinding(
            queue="queue.projector",
            routing_pattern="tasks.#",
            dlq_queue="queue.projector.dlq",
        ),
        QueueBinding(queue="webhook", routing_pattern="webhook", dlq_queue="webhook.dlq"),
    )


class RabbitMQRelayError(RuntimeError):
    """Raised for relay-layer publish/connect failures."""


class RabbitMQRelay:
    """Small blocking wrapper around a confirmed publisher channel."""

    def __init__(
        self,
        *,
        rabbitmq_url: str,
        socket_connect_timeout: float = 3.0,
        heartbeat: int = 60,
    ) -> None:
        self._rabbitmq_url = rabbitmq_url
        self._socket_connect_timeout = socket_connect_timeout
        self._heartbeat = heartbeat
        self._connection: Any | None = None
        self._channel: Any | None = None
        self._topology_declared = False

    def _ensure_client(self) -> None:
        if pika is None:
            raise RabbitMQRelayError("pika is not installed; add `pika` to solution2 dependencies")

    def connect(self) -> None:
        """Open a confirmed RabbitMQ channel."""
        self._ensure_client()
        if self._connection is not None:
            return
        if pika is None:
            raise RabbitMQRelayError("pika is unavailable")

        parameters = pika.URLParameters(self._rabbitmq_url)
        parameters.heartbeat = self._heartbeat
        parameters.blocked_connection_timeout = self._socket_connect_timeout
        parameters.socket_timeout = self._socket_connect_timeout
        self._connection = pika.BlockingConnection(parameters=parameters)
        channel = self._connection.channel()
        channel.confirm_delivery()
        self._channel = channel
        logger.info("rabbitmq_connected", heartbeat=self._heartbeat)

    def close(self) -> None:
        """Close channel and connection."""
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("rabbitmq_channel_close_failed", error=str(exc))
            self._channel = None
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.warning("rabbitmq_connection_close_failed", error=str(exc))
            self._connection = None
        self._topology_declared = False

    def ensure_topology(self) -> None:
        """Create exchange/queues once per process."""
        self._ensure_connected()
        if self._topology_declared:
            return
        if self._channel is None:
            raise RabbitMQRelayError("channel not initialized")
        channel = self._channel

        channel.exchange_declare(exchange=OUTBOX_EXCHANGE_NAME, exchange_type="topic", durable=True)
        channel.exchange_declare(exchange=OUTBOX_DLX_NAME, exchange_type="topic", durable=True)

        for binding in _default_bindings():
            channel.queue_declare(
                queue=binding.queue,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": OUTBOX_DLX_NAME,
                    "x-dead-letter-routing-key": f"{binding.dlq_queue}",
                },
            )
            channel.queue_bind(
                exchange=OUTBOX_EXCHANGE_NAME,
                queue=binding.queue,
                routing_key=binding.routing_pattern,
            )
            channel.queue_declare(queue=binding.dlq_queue, durable=True, arguments=None)
            channel.queue_bind(
                exchange=OUTBOX_DLX_NAME,
                queue=binding.dlq_queue,
                routing_key=binding.dlq_queue,
            )
        self._topology_declared = True
        logger.info("rabbitmq_topology_declared", topics=OUTBOX_TOPICS)

    def publish(self, *, event_id: str, routing_key: str, payload: dict[str, Any]) -> None:
        """Publish a confirmed message for exactly-once-at-most-once attempts."""
        self._ensure_connected()
        if self._channel is None:
            raise RabbitMQRelayError("channel not initialized")
        properties = (
            pika.BasicProperties(
                message_id=event_id,
                content_type="application/json",
                delivery_mode=2,
                timestamp=int(time.time()),
                app_id="solution2",
            )
            if pika is not None
            else SimpleNamespace(message_id=event_id, content_type="application/json")
        )
        body = json.dumps(payload, separators=(",", ":"))
        accepted = self._channel.basic_publish(
            exchange=OUTBOX_EXCHANGE_NAME,
            routing_key=routing_key,
            body=body,
            properties=properties,
            mandatory=True,
        )
        if accepted is False:
            raise RabbitMQRelayError(f"outbox publish not accepted for event_id={event_id}")

    def _ensure_connected(self) -> None:
        if self._connection is None or self._channel is None:
            raise RabbitMQRelayError("rabbitmq is not connected")


def build_default_bindings() -> tuple[QueueBinding, ...]:
    """Return default relay bindings for tests and service bootstrap."""
    return _default_bindings()
