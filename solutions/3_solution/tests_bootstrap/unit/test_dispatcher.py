from __future__ import annotations

import json

import pika
import pytest

from solution3.constants import (
    RABBITMQ_EXCHANGE_COLDSTART,
    RABBITMQ_EXCHANGE_PRELOADED,
    RABBITMQ_QUEUE_COLD,
)
from solution3.workers import dispatcher


class FakeChannel:
    def __init__(self) -> None:
        self.exchange_declarations: list[dict[str, object]] = []
        self.queue_declarations: list[dict[str, object]] = []
        self.queue_bindings: list[dict[str, object]] = []
        self.publish_calls: list[dict[str, object]] = []
        self.publish_result: bool | None = True

    def exchange_declare(
        self,
        *,
        exchange: str,
        exchange_type: str,
        durable: bool,
        arguments: dict[str, str] | None = None,
    ) -> None:
        self.exchange_declarations.append(
            {
                "exchange": exchange,
                "exchange_type": exchange_type,
                "durable": durable,
                "arguments": arguments,
            }
        )

    def queue_declare(self, *, queue: str, durable: bool) -> None:
        self.queue_declarations.append({"queue": queue, "durable": durable})

    def queue_bind(self, *, queue: str, exchange: str, arguments: dict[str, str]) -> None:
        self.queue_bindings.append({"queue": queue, "exchange": exchange, "arguments": arguments})

    def basic_publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: pika.BasicProperties,
    ) -> bool | None:
        self.publish_calls.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body": body,
                "properties": properties,
            }
        )
        return self.publish_result


def test_declare_dispatch_topology_sets_up_exchanges_and_cold_queue() -> None:
    channel = FakeChannel()

    dispatcher.declare_dispatch_topology(channel)

    assert channel.exchange_declarations == [
        {
            "exchange": RABBITMQ_EXCHANGE_COLDSTART,
            "exchange_type": "headers",
            "durable": True,
            "arguments": None,
        },
        {
            "exchange": RABBITMQ_EXCHANGE_PRELOADED,
            "exchange_type": "headers",
            "durable": True,
            "arguments": {"alternate-exchange": RABBITMQ_EXCHANGE_COLDSTART},
        },
    ]
    assert channel.queue_declarations == [{"queue": RABBITMQ_QUEUE_COLD, "durable": True}]
    assert channel.queue_bindings == [
        {
            "queue": RABBITMQ_QUEUE_COLD,
            "exchange": RABBITMQ_EXCHANGE_COLDSTART,
            "arguments": {"x-match": "all"},
        }
    ]


def test_dispatch_requested_task_publishes_durable_message_with_headers() -> None:
    channel = FakeChannel()
    payload = dispatcher.encode_task_requested_event(
        {
            "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
            "model_class": "small",
            "tier": "pro",
            "x": 1,
            "y": 2,
        }
    )

    dispatcher.dispatch_requested_task(
        channel=channel,
        event={
            "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
            "model_class": "small",
            "tier": "pro",
        },
        raw_payload=payload,
    )

    assert len(channel.publish_calls) == 1
    publish_call = channel.publish_calls[0]
    properties = publish_call["properties"]
    assert isinstance(properties, pika.BasicProperties)
    assert publish_call["exchange"] == RABBITMQ_EXCHANGE_PRELOADED
    assert publish_call["routing_key"] == ""
    assert publish_call["body"] == payload
    assert properties.delivery_mode == 2
    assert properties.content_type == "application/json"
    assert properties.headers == {
        "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
        "model_class": "small",
        "tier": "pro",
    }


def test_dispatch_requested_task_rejects_missing_headers() -> None:
    with pytest.raises(ValueError, match="task_id, model_class, and tier are required"):
        dispatcher.dispatch_requested_task(
            channel=FakeChannel(),
            event={"task_id": "abc", "model_class": "small"},
            raw_payload=b"{}",
        )


def test_dispatch_requested_task_raises_when_broker_rejects_publish() -> None:
    channel = FakeChannel()
    channel.publish_result = False

    with pytest.raises(RuntimeError, match="dispatcher publish was rejected"):
        dispatcher.dispatch_requested_task(
            channel=channel,
            event={"task_id": "abc", "model_class": "small", "tier": "pro"},
            raw_payload=b"{}",
        )


def test_encode_task_requested_event_is_stable_json() -> None:
    payload = dispatcher.encode_task_requested_event(
        {"model_class": "small", "task_id": "abc", "tier": "pro"}
    )
    assert json.loads(payload.decode("utf-8")) == {
        "model_class": "small",
        "task_id": "abc",
        "tier": "pro",
    }
