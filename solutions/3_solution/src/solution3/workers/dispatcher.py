from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

import pika

from solution3.constants import (
    RABBITMQ_EXCHANGE_COLDSTART,
    RABBITMQ_EXCHANGE_PRELOADED,
    RABBITMQ_QUEUE_COLD,
)
from solution3.workers._bootstrap_worker import run_worker


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
        raise RuntimeError("dispatcher publish was rejected")


def encode_task_requested_event(event: Mapping[str, object]) -> bytes:
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")


def main() -> None:
    run_worker(name="solution3_dispatcher")


if __name__ == "__main__":
    main()
