from __future__ import annotations

import argparse
import json
import signal
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Protocol, cast

import pika
from prometheus_client import start_http_server

from solution3.constants import (
    RABBITMQ_EXCHANGE_COLDSTART,
    RABBITMQ_EXCHANGE_PRELOADED,
    RABBITMQ_QUEUE_COLD,
)
from solution3.core.settings import load_settings
from solution3.observability.metrics import TASK_DISPATCHES_TOTAL
from solution3.utils.logging import configure_logging, get_logger

try:
    from kafka import KafkaConsumer
except ImportError:  # pragma: no cover - explicit runtime guard tested via monkeypatch
    KafkaConsumer = None

logger = get_logger("solution3.workers.dispatcher")


class RabbitMQChannel(Protocol):
    def exchange_declare(
        self,
        *,
        exchange: str,
        exchange_type: str,
        durable: bool,
        arguments: dict[str, str] | None = None,
    ) -> None: ...

    def queue_declare(self, *, queue: str, durable: bool) -> None: ...

    def queue_bind(
        self,
        *,
        queue: str,
        exchange: str,
        arguments: dict[str, str],
    ) -> None: ...

    def basic_publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: pika.BasicProperties,
    ) -> bool | None: ...


class RabbitMQConnection(Protocol):
    def channel(self) -> RabbitMQChannel: ...

    def close(self) -> None: ...


class KafkaMessage(Protocol):
    value: bytes


class DispatcherConsumer(Protocol):
    def poll(
        self, *, timeout_ms: int, max_records: int
    ) -> Mapping[object, Sequence[KafkaMessage]]: ...

    def commit(self) -> None: ...

    def close(self) -> None: ...

    def subscribe(self, topics: list[str]) -> None: ...


class DispatcherConsumerSettings(Protocol):
    redpanda_bootstrap_servers: str
    redpanda_topic_task_requested: str


class DispatcherRabbitSettings(Protocol):
    rabbitmq_url: str


class DispatcherRuntimeSettings(DispatcherConsumerSettings, DispatcherRabbitSettings, Protocol):
    dispatcher_metrics_port: int


def declare_dispatch_topology(channel: RabbitMQChannel) -> None:
    channel.exchange_declare(
        exchange=RABBITMQ_EXCHANGE_COLDSTART,
        exchange_type="headers",
        durable=True,
    )
    channel.exchange_declare(
        exchange=RABBITMQ_EXCHANGE_PRELOADED,
        exchange_type="headers",
        durable=True,
        arguments={"alternate-exchange": RABBITMQ_EXCHANGE_COLDSTART},
    )
    channel.queue_declare(queue=RABBITMQ_QUEUE_COLD, durable=True)
    channel.queue_bind(
        queue=RABBITMQ_QUEUE_COLD,
        exchange=RABBITMQ_EXCHANGE_COLDSTART,
        arguments={"x-match": "all"},
    )


def dispatch_requested_task(
    *,
    channel: RabbitMQChannel,
    event: Mapping[str, str],
    raw_payload: bytes,
) -> None:
    task_id = event.get("task_id")
    model_class = event.get("model_class")
    tier = event.get("tier")
    if not task_id or not model_class or not tier:
        raise ValueError("task_id, model_class, and tier are required")

    accepted = channel.basic_publish(
        exchange=RABBITMQ_EXCHANGE_PRELOADED,
        routing_key="",
        body=raw_payload,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers={
                "task_id": task_id,
                "model_class": model_class,
                "tier": tier,
            },
        ),
    )
    if accepted is False:
        TASK_DISPATCHES_TOTAL.labels(result="rejected").inc()
        raise RuntimeError("dispatcher publish was rejected")
    TASK_DISPATCHES_TOTAL.labels(result="ok").inc()


def encode_task_requested_event(event: Mapping[str, object]) -> bytes:
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")


def build_redpanda_consumer(settings: DispatcherConsumerSettings) -> DispatcherConsumer:
    if KafkaConsumer is None:
        raise RuntimeError("kafka-python is not installed")

    bootstrap_servers = [
        server.strip()
        for server in settings.redpanda_bootstrap_servers.split(",")
        if server.strip()
    ]
    if not bootstrap_servers:
        raise RuntimeError("redpanda bootstrap servers are not configured")

    consumer = KafkaConsumer(
        bootstrap_servers=bootstrap_servers,
        group_id="solution3-dispatcher",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    consumer.subscribe([settings.redpanda_topic_task_requested])
    return cast(DispatcherConsumer, consumer)


def build_rabbitmq_channel(
    settings: DispatcherRabbitSettings,
) -> tuple[RabbitMQConnection, RabbitMQChannel]:
    parameters = pika.URLParameters(settings.rabbitmq_url)
    parameters.heartbeat = 60
    parameters.blocked_connection_timeout = 3.0
    parameters.socket_timeout = 3.0
    connection = pika.BlockingConnection(parameters=parameters)
    channel = connection.channel()
    return connection, channel


def open_dispatch_resources(
    settings: DispatcherRuntimeSettings,
) -> tuple[DispatcherConsumer, RabbitMQConnection, RabbitMQChannel]:
    consumer = build_redpanda_consumer(settings)
    connection, channel = build_rabbitmq_channel(settings)
    declare_dispatch_topology(channel)
    return consumer, connection, channel


def close_dispatch_resources(
    *,
    consumer: DispatcherConsumer | None,
    connection: RabbitMQConnection | None,
) -> None:
    if consumer is not None:
        with suppress(Exception):
            consumer.close()
    if connection is not None:
        with suppress(Exception):
            connection.close()


def dispatch_polled_messages(
    *,
    consumer: DispatcherConsumer,
    channel: RabbitMQChannel,
    poll_timeout_ms: int,
    max_records: int,
) -> int:
    polled = consumer.poll(timeout_ms=poll_timeout_ms, max_records=max_records)
    messages = [message for batch in polled.values() for message in batch]
    if not messages:
        return 0

    for message in messages:
        event = json.loads(message.value.decode("utf-8"))
        if not isinstance(event, dict):
            raise ValueError("dispatcher message payload must decode to an object")
        dispatch_requested_task(
            channel=channel,
            event=event,
            raw_payload=message.value,
        )

    consumer.commit()
    return len(messages)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="solution3 dispatcher")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--poll-timeout-ms", type=int, default=1000)
    parser.add_argument("--max-records", type=int, default=100)
    return parser.parse_args()


def _main_loop(*, interval_seconds: float, poll_timeout_ms: int, max_records: int) -> None:
    settings = load_settings()
    start_http_server(settings.dispatcher_metrics_port)
    stop_requested = False
    consumer: DispatcherConsumer | None = None
    connection: RabbitMQConnection | None = None
    channel: RabbitMQChannel | None = None

    def _stop(*_args: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    logger.info(
        "dispatcher_started",
        interval_seconds=interval_seconds,
        poll_timeout_ms=poll_timeout_ms,
        max_records=max_records,
    )
    try:
        while not stop_requested:
            try:
                if consumer is None or connection is None or channel is None:
                    consumer, connection, channel = open_dispatch_resources(settings)
                dispatched = dispatch_polled_messages(
                    consumer=consumer,
                    channel=channel,
                    poll_timeout_ms=poll_timeout_ms,
                    max_records=max_records,
                )
                if dispatched > 0:
                    logger.info("dispatcher_batch_dispatched", count=dispatched)
                    continue
            except Exception as exc:
                logger.exception("dispatcher_iteration_failed", error=str(exc))
                close_dispatch_resources(consumer=consumer, connection=connection)
                consumer = None
                connection = None
                channel = None

            time.sleep(interval_seconds)
    finally:
        close_dispatch_resources(consumer=consumer, connection=connection)
        logger.info("dispatcher_stopped")


def main() -> None:
    args = _parse_args()
    configure_logging(enable_sensitive=False)
    _main_loop(
        interval_seconds=max(float(args.interval), 0.1),
        poll_timeout_ms=max(int(args.poll_timeout_ms), 1),
        max_records=max(int(args.max_records), 1),
    )


if __name__ == "__main__":
    main()
