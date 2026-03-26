from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pika
import pytest

from solution3.constants import (
    RABBITMQ_EXCHANGE_COLDSTART,
    RABBITMQ_EXCHANGE_PRELOADED,
    RABBITMQ_QUEUE_COLD,
)
from solution3.workers import dispatcher


class FakeMessage:
    def __init__(self, payload: bytes) -> None:
        self.value = payload


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


class FakeConsumer:
    def __init__(self, records: dict[object, list[FakeMessage]] | None = None) -> None:
        self.records = records or {}
        self.poll_calls: list[tuple[int, int]] = []
        self.commit_calls = 0
        self.closed = False
        self.subscribed_topics: list[str] = []

    def poll(self, *, timeout_ms: int, max_records: int) -> dict[object, list[FakeMessage]]:
        self.poll_calls.append((timeout_ms, max_records))
        return self.records

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.closed = True

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics.extend(topics)


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


def test_dispatch_polled_messages_publishes_and_commits_batch() -> None:
    payload = dispatcher.encode_task_requested_event(
        {"task_id": "abc", "model_class": "small", "tier": "pro", "x": 1, "y": 2}
    )
    consumer = FakeConsumer(records={object(): [FakeMessage(payload)]})
    channel = FakeChannel()

    dispatched = dispatcher.dispatch_polled_messages(
        consumer=consumer,
        channel=channel,
        poll_timeout_ms=250,
        max_records=10,
    )

    assert dispatched == 1
    assert consumer.poll_calls == [(250, 10)]
    assert consumer.commit_calls == 1
    assert len(channel.publish_calls) == 1


def test_dispatch_polled_messages_does_not_commit_when_publish_fails() -> None:
    payload = dispatcher.encode_task_requested_event(
        {"task_id": "abc", "model_class": "small", "tier": "pro"}
    )
    consumer = FakeConsumer(records={object(): [FakeMessage(payload)]})
    channel = FakeChannel()
    channel.publish_result = False

    with pytest.raises(RuntimeError, match="dispatcher publish was rejected"):
        dispatcher.dispatch_polled_messages(
            consumer=consumer,
            channel=channel,
            poll_timeout_ms=250,
            max_records=10,
        )

    assert consumer.commit_calls == 0


def test_build_redpanda_consumer_uses_expected_group_and_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeKafkaConsumer(FakeConsumer):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(dispatcher, "KafkaConsumer", FakeKafkaConsumer)

    consumer = dispatcher.build_redpanda_consumer(
        SimpleNamespace(
            redpanda_bootstrap_servers="redpanda:9092",
            redpanda_topic_task_requested="tasks.requested",
        )
    )

    typed_consumer = cast(FakeConsumer, consumer)
    assert captured["bootstrap_servers"] == ["redpanda:9092"]
    assert captured["group_id"] == "solution3-dispatcher"
    assert captured["enable_auto_commit"] is False
    assert typed_consumer.subscribed_topics == ["tasks.requested"]


def test_main_configures_logging_and_runs_dispatch_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    main_loop_calls: list[tuple[float, int, int]] = []

    def fake_main_loop(*, interval_seconds: float, poll_timeout_ms: int, max_records: int) -> None:
        main_loop_calls.append((interval_seconds, poll_timeout_ms, max_records))

    def fake_configure_logging(*, enable_sensitive: bool) -> None:
        configure_calls.append(enable_sensitive)

    monkeypatch.setattr(
        dispatcher,
        "_parse_args",
        lambda: SimpleNamespace(interval=2.5, poll_timeout_ms=250, max_records=25),
    )
    monkeypatch.setattr(dispatcher, "_main_loop", fake_main_loop)
    monkeypatch.setattr(dispatcher, "configure_logging", fake_configure_logging)

    dispatcher.main()

    assert configure_calls == [False]
    assert main_loop_calls == [(2.5, 250, 25)]
