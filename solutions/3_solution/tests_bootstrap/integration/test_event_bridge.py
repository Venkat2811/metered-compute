from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest
from kafka import KafkaConsumer

from solution3.constants import RABBITMQ_EXCHANGE_COLDSTART, RABBITMQ_EXCHANGE_PRELOADED
from solution3.workers import dispatcher


def _postgres_dsn() -> str:
    return os.environ.get(
        "SOLUTION3_TEST_POSTGRES_DSN",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )


def _rabbitmq_url() -> str:
    return os.environ.get(
        "SOLUTION3_TEST_RABBITMQ_URL",
        "amqp://guest:guest@localhost:5672/",
    )


def _redpanda_bootstrap() -> str:
    return os.environ.get("SOLUTION3_TEST_REDPANDA_BOOTSTRAP", "localhost:19092")


async def _insert_outbox_event(
    *, event_id: str, aggregate_id: str, topic: str, payload: str
) -> None:
    connection = await asyncpg.connect(dsn=_postgres_dsn())
    try:
        await connection.execute(
            """
            INSERT INTO cmd.outbox_events(event_id, aggregate_id, event_type, topic, payload)
            VALUES($1::uuid, $2::uuid, 'task.requested', $3, $4::jsonb)
            """,
            event_id,
            aggregate_id,
            topic,
            payload,
        )
    finally:
        await connection.close()


async def _wait_for_published_at(*, event_id: str, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    connection = await asyncpg.connect(dsn=_postgres_dsn())
    try:
        while time.time() < deadline:
            published_at = await connection.fetchval(
                "SELECT published_at FROM cmd.outbox_events WHERE event_id=$1::uuid",
                event_id,
            )
            if published_at is not None:
                return True
            await asyncio.sleep(0.2)
        return False
    finally:
        await connection.close()


@pytest.mark.integration
def test_outbox_relay_and_dispatcher_bridge_over_live_services() -> None:
    task_id = str(uuid4())
    event_id = str(uuid4())
    aggregate_id = str(uuid4())
    topic = f"tasks.requested.integration.{uuid4()}"
    payload = {
        "task_id": task_id,
        "user_id": str(uuid4()),
        "tier": "pro",
        "mode": "async",
        "model_class": "small",
        "x": 1,
        "y": 2,
        "cost": 10,
        "tb_pending_transfer_id": str(uuid4()),
    }
    probe_queue = f"itest-dispatch-probe-{uuid4()}"

    connection, channel = dispatcher.build_rabbitmq_channel(
        SimpleNamespace(rabbitmq_url=_rabbitmq_url())
    )
    dispatcher.declare_dispatch_topology(channel)
    raw_channel = cast(Any, channel)
    raw_channel.queue_declare(queue=probe_queue, durable=False, auto_delete=True)
    raw_channel.queue_bind(
        queue=probe_queue,
        exchange=RABBITMQ_EXCHANGE_PRELOADED,
        arguments={"x-match": "all"},
    )

    consumer: KafkaConsumer | None = None
    try:
        asyncio.run(
            _insert_outbox_event(
                event_id=event_id,
                aggregate_id=aggregate_id,
                topic=topic,
                payload=json.dumps(payload),
            )
        )
        published = asyncio.run(_wait_for_published_at(event_id=event_id, timeout_seconds=10.0))
        assert published is True

        consumer = KafkaConsumer(
            bootstrap_servers=[_redpanda_bootstrap()],
            group_id=f"itest-dispatcher-{uuid4()}",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        consumer.subscribe([topic])

        deadline = time.time() + 10.0
        dispatched = 0
        while time.time() < deadline and dispatched == 0:
            dispatched = dispatcher.dispatch_polled_messages(
                consumer=consumer,
                channel=channel,
                poll_timeout_ms=500,
                max_records=10,
            )
        assert dispatched == 1

        deadline = time.time() + 10.0
        method_frame = None
        header_frame = None
        body = None
        while time.time() < deadline:
            method_frame, header_frame, body = raw_channel.basic_get(
                queue=probe_queue,
                auto_ack=True,
            )
            if method_frame is None:
                time.sleep(0.2)
                continue

            if header_frame is not None and header_frame.headers.get("task_id") == task_id:
                break

            method_frame = None
            header_frame = None
            body = None

        assert method_frame is not None
        assert header_frame is not None
        assert body is not None
        assert json.loads(body.decode("utf-8")) == payload
        assert header_frame.headers["task_id"] == task_id
        assert header_frame.headers["model_class"] == "small"
        assert header_frame.headers["tier"] == "pro"
    finally:
        if consumer is not None:
            consumer.close(autocommit=False, timeout_ms=1000)
        raw_channel.queue_delete(queue=probe_queue)
        connection.close()


@pytest.mark.integration
def test_dispatch_prefers_hot_binding_over_cold_fallback() -> None:
    task_id = str(uuid4())
    hot_queue = f"itest-hot-{uuid4()}"
    cold_queue = f"itest-cold-{uuid4()}"
    payload = dispatcher.encode_task_requested_event(
        {
            "task_id": task_id,
            "user_id": str(uuid4()),
            "tier": "pro",
            "mode": "async",
            "model_class": "small",
            "x": 1,
            "y": 2,
            "cost": 10,
            "tb_pending_transfer_id": str(uuid4()),
        }
    )

    connection, channel = dispatcher.build_rabbitmq_channel(
        SimpleNamespace(rabbitmq_url=_rabbitmq_url())
    )
    dispatcher.declare_dispatch_topology(channel)
    raw_channel = cast(Any, channel)
    raw_channel.queue_declare(queue=hot_queue, durable=False, auto_delete=True)
    raw_channel.queue_bind(
        queue=hot_queue,
        exchange=RABBITMQ_EXCHANGE_PRELOADED,
        arguments={"x-match": "all", "model_class": "small"},
    )
    raw_channel.queue_declare(queue=cold_queue, durable=False, auto_delete=True)
    raw_channel.queue_bind(
        queue=cold_queue,
        exchange=RABBITMQ_EXCHANGE_COLDSTART,
        arguments={"x-match": "all"},
    )

    try:
        dispatcher.dispatch_requested_task(
            channel=channel,
            event={"task_id": task_id, "model_class": "small", "tier": "pro"},
            raw_payload=payload,
        )

        hot_message = raw_channel.basic_get(queue=hot_queue, auto_ack=True)
        cold_message = raw_channel.basic_get(queue=cold_queue, auto_ack=True)

        assert hot_message[0] is not None
        assert hot_message[2] == payload
        assert cold_message[0] is None
    finally:
        raw_channel.queue_delete(queue=hot_queue)
        raw_channel.queue_delete(queue=cold_queue)
        connection.close()


@pytest.mark.integration
def test_dispatch_falls_back_to_cold_when_no_hot_binding_exists() -> None:
    task_id = str(uuid4())
    cold_queue = f"itest-cold-{uuid4()}"
    payload = dispatcher.encode_task_requested_event(
        {
            "task_id": task_id,
            "user_id": str(uuid4()),
            "tier": "pro",
            "mode": "async",
            "model_class": "large",
            "x": 3,
            "y": 4,
            "cost": 50,
            "tb_pending_transfer_id": str(uuid4()),
        }
    )

    connection, channel = dispatcher.build_rabbitmq_channel(
        SimpleNamespace(rabbitmq_url=_rabbitmq_url())
    )
    dispatcher.declare_dispatch_topology(channel)
    raw_channel = cast(Any, channel)
    raw_channel.queue_declare(queue=cold_queue, durable=False, auto_delete=True)
    raw_channel.queue_bind(
        queue=cold_queue,
        exchange=RABBITMQ_EXCHANGE_COLDSTART,
        arguments={"x-match": "all"},
    )

    try:
        dispatcher.dispatch_requested_task(
            channel=channel,
            event={"task_id": task_id, "model_class": "large", "tier": "enterprise"},
            raw_payload=payload,
        )

        cold_message = raw_channel.basic_get(queue=cold_queue, auto_ack=True)

        assert cold_message[0] is not None
        assert cold_message[2] == payload
    finally:
        raw_channel.queue_delete(queue=cold_queue)
        connection.close()
