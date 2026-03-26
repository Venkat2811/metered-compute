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

    connection, channel = dispatcher.build_rabbitmq_channel(
        SimpleNamespace(rabbitmq_url=_rabbitmq_url())
    )
    dispatcher.declare_dispatch_topology(channel)
    raw_channel = cast(Any, channel)
    raw_channel.queue_purge(queue="cold")

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
        while time.time() < deadline and method_frame is None:
            method_frame, header_frame, body = raw_channel.basic_get(queue="cold", auto_ack=True)
            if method_frame is None:
                time.sleep(0.2)

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
        connection.close()
