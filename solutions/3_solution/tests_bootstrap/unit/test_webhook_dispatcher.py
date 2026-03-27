from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from solution3.workers import webhook_dispatcher


class FakeMessage:
    def __init__(
        self,
        *,
        topic: str,
        payload: dict[str, object],
        event_id: str | None = None,
    ) -> None:
        self.topic: str = topic
        self.partition: int = 0
        self.offset: int = 0
        self.value: bytes = json.dumps(payload).encode("utf-8")
        header_value = event_id if event_id is not None else str(uuid4())
        self.headers: Sequence[tuple[str, bytes | str | None]] = [
            ("event_id", header_value.encode("utf-8"))
        ]


class FakeConsumer:
    def __init__(self, records: Mapping[object, Sequence[FakeMessage]] | None = None) -> None:
        self.records: Mapping[object, Sequence[FakeMessage]] = records or {}
        self.poll_calls: list[tuple[int, int]] = []
        self.commit_calls = 0
        self.closed = False
        self.subscribed_topics: list[str] = []

    def poll(self, *, timeout_ms: int, max_records: int) -> Mapping[object, Sequence[FakeMessage]]:
        self.poll_calls.append((timeout_ms, max_records))
        return self.records

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.closed = True

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics.extend(topics)


class FakeResponse:
    def __init__(self, *, status_code: int) -> None:
        self.status_code: int = status_code


class FakeHttpClient:
    def __init__(self, responses: list[int | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
    ) -> FakeResponse:
        self.calls.append((url, json, headers))
        if not self._responses:
            return FakeResponse(status_code=200)
        next_response = self._responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        return FakeResponse(status_code=next_response)

    async def aclose(self) -> None:
        return None


def _settings() -> Any:
    return SimpleNamespace(
        redpanda_bootstrap_servers="redpanda:9092",
        redpanda_topic_task_completed="tasks.completed",
        redpanda_topic_task_failed="tasks.failed",
        redpanda_topic_task_cancelled="tasks.cancelled",
        redpanda_topic_task_expired="tasks.expired",
        webhook_delivery_timeout_seconds=3.0,
        webhook_max_attempts=3,
        webhook_initial_backoff_seconds=0.01,
        webhook_max_backoff_seconds=0.05,
    )


def test_parse_args_reads_cli_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "argparse.ArgumentParser.parse_args",
        lambda self: SimpleNamespace(
            interval=2.5,
            poll_timeout_ms=250,
            max_records=5,
            max_attempts=4,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=6.0,
        ),
    )

    args = webhook_dispatcher._parse_args()

    assert args.interval == 2.5
    assert args.poll_timeout_ms == 250
    assert args.max_records == 5
    assert args.max_attempts == 4
    assert args.initial_backoff_seconds == 0.5
    assert args.max_backoff_seconds == 6.0


def test_decode_header_value_handles_bytes_strings_and_missing_values() -> None:
    headers: Sequence[tuple[str, bytes | str | None]] = [
        ("x", None),
        ("event_id", b"abc"),
        ("other", "ignored"),
    ]

    assert webhook_dispatcher._decode_header_value(headers, "event_id") == "abc"
    assert webhook_dispatcher._decode_header_value([("event_id", "xyz")], "event_id") == "xyz"
    assert webhook_dispatcher._decode_header_value(headers, "missing") is None


def test_next_backoff_seconds_doubles_and_caps() -> None:
    assert (
        webhook_dispatcher._next_backoff_seconds(
            attempt_number=1,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=10.0,
        )
        == 0.5
    )
    assert (
        webhook_dispatcher._next_backoff_seconds(
            attempt_number=3,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=10.0,
        )
        == 2.0
    )
    assert (
        webhook_dispatcher._next_backoff_seconds(
            attempt_number=8,
            initial_backoff_seconds=1.0,
            max_backoff_seconds=4.0,
        )
        == 4.0
    )


def test_terminal_webhook_payload_projects_expected_fields() -> None:
    payload = webhook_dispatcher._terminal_webhook_payload(
        {
            "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
            "status": "FAILED",
            "billing_state": "RELEASED",
            "result": None,
            "error": "boom",
        }
    )

    assert payload == {
        "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
        "status": "FAILED",
        "billing_state": "RELEASED",
        "result": None,
        "error": "boom",
    }


@pytest.mark.asyncio
async def test_process_message_skips_when_task_has_no_callback_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_task_callback_url(*_: object, **__: object) -> str | None:
        return None

    insert_calls: list[object] = []

    async def fake_insert_webhook_dead_letter(*_: object, **__: object) -> None:
        insert_calls.append(object())

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_task_callback_url",
        fake_get_task_callback_url,
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "insert_webhook_dead_letter",
        fake_insert_webhook_dead_letter,
    )

    handled = await webhook_dispatcher.process_message(
        db_pool=cast(Any, object()),
        http_client=FakeHttpClient([200]),
        message=FakeMessage(
            topic="tasks.completed",
            payload={
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "status": "COMPLETED",
                "billing_state": "CAPTURED",
                "result": {"sum": 5},
            },
        ),
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
        sleep=cast(Any, lambda _seconds: None),
    )

    assert handled is True
    assert insert_calls == []


@pytest.mark.asyncio
async def test_process_message_posts_terminal_payload_when_callback_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = FakeHttpClient([200])

    async def fake_get_task_callback_url(*_: object, **__: object) -> str | None:
        return "https://example.test/webhook"

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_task_callback_url",
        fake_get_task_callback_url,
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "ensure_callback_url_delivery_safe",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    handled = await webhook_dispatcher.process_message(
        db_pool=cast(Any, object()),
        http_client=http_client,
        message=FakeMessage(
            topic="tasks.completed",
            payload={
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "status": "COMPLETED",
                "billing_state": "CAPTURED",
                "result": {"sum": 5},
            },
        ),
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
        sleep=cast(Any, lambda _seconds: None),
    )

    assert handled is True
    assert len(http_client.calls) == 1
    url, payload, headers = http_client.calls[0]
    assert url == "https://example.test/webhook"
    assert payload == {
        "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
        "status": "COMPLETED",
        "billing_state": "CAPTURED",
        "result": {"sum": 5},
        "error": None,
    }
    assert "X-Webhook-Event-Id" in headers


@pytest.mark.asyncio
async def test_process_message_dead_letters_after_max_retryable_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = FakeHttpClient([503, 503, 503])
    sleeps: list[float] = []
    inserted: list[dict[str, object]] = []

    async def fake_get_task_callback_url(*_: object, **__: object) -> str | None:
        return "https://example.test/webhook"

    async def fake_insert_webhook_dead_letter(*_: object, **kwargs: object) -> None:
        inserted.append(dict(kwargs))

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_task_callback_url",
        fake_get_task_callback_url,
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "insert_webhook_dead_letter",
        fake_insert_webhook_dead_letter,
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "ensure_callback_url_delivery_safe",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    handled = await webhook_dispatcher.process_message(
        db_pool=cast(Any, object()),
        http_client=http_client,
        message=FakeMessage(
            topic="tasks.failed",
            payload={
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "status": "FAILED",
                "billing_state": "RELEASED",
                "error": "boom",
            },
        ),
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
        sleep=fake_sleep,
    )

    assert handled is True
    assert len(http_client.calls) == 3
    assert sleeps == [0.01, 0.02]
    assert len(inserted) == 1
    assert inserted[0]["attempts"] == 3
    assert inserted[0]["topic"] == "tasks.failed"
    assert inserted[0]["callback_url"] == "https://example.test/webhook"


@pytest.mark.asyncio
async def test_process_message_dead_letters_unsafe_callback_without_http_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = FakeHttpClient([200])
    inserted: list[dict[str, object]] = []

    async def fake_get_task_callback_url(*_: object, **__: object) -> str | None:
        return "http://127.0.0.1:8080/internal"

    async def fake_insert_webhook_dead_letter(*_: object, **kwargs: object) -> None:
        inserted.append(dict(kwargs))

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_task_callback_url",
        fake_get_task_callback_url,
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "insert_webhook_dead_letter",
        fake_insert_webhook_dead_letter,
    )

    handled = await webhook_dispatcher.process_message(
        db_pool=cast(Any, object()),
        http_client=http_client,
        message=FakeMessage(
            topic="tasks.failed",
            payload={
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "status": "FAILED",
                "billing_state": "RELEASED",
                "error": "boom",
            },
        ),
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )

    assert handled is True
    assert http_client.calls == []
    assert len(inserted) == 1
    assert inserted[0]["callback_url"] == "http://127.0.0.1:8080/internal"
    assert inserted[0]["attempts"] == 0
    assert inserted[0]["last_error"] == "unsafe_callback_url"


@pytest.mark.asyncio
async def test_deliver_with_retries_returns_after_non_retryable_response() -> None:
    http_client = FakeHttpClient([400])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    delivered, attempts, error = await webhook_dispatcher._deliver_with_retries(
        http_client=http_client,
        callback_url="https://example.test/webhook",
        event_id=uuid4(),
        payload={"task_id": "abc"},
        max_attempts=3,
        initial_backoff_seconds=0.5,
        max_backoff_seconds=4.0,
        sleep=fake_sleep,
    )

    assert delivered is False
    assert attempts == 1
    assert error == "status=400"
    assert sleeps == []


@pytest.mark.asyncio
async def test_deliver_with_retries_retries_exception_then_succeeds() -> None:
    http_client = FakeHttpClient([RuntimeError("boom"), 200])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    delivered, attempts, error = await webhook_dispatcher._deliver_with_retries(
        http_client=http_client,
        callback_url="https://example.test/webhook",
        event_id=uuid4(),
        payload={"task_id": "abc"},
        max_attempts=3,
        initial_backoff_seconds=0.5,
        max_backoff_seconds=4.0,
        sleep=fake_sleep,
    )

    assert delivered is True
    assert attempts == 2
    assert error is None
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_process_message_rejects_non_object_payload() -> None:
    class _BadMessage:
        topic = "tasks.completed"
        partition = 0
        offset = 0
        value = b'["not-an-object"]'
        headers: Sequence[tuple[str, bytes | str | None]] = [("event_id", b"abc")]

    with pytest.raises(ValueError, match="decode to an object"):
        await webhook_dispatcher.process_message(
            db_pool=cast(Any, object()),
            http_client=cast(Any, FakeHttpClient([200])),
            message=cast(Any, _BadMessage()),
            max_attempts=3,
            initial_backoff_seconds=0.01,
            max_backoff_seconds=0.05,
        )


@pytest.mark.asyncio
async def test_process_message_requires_event_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _MissingHeaderMessage:
        topic = "tasks.completed"
        partition = 0
        offset = 0
        value = json.dumps(
            {
                "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                "status": "COMPLETED",
                "billing_state": "CAPTURED",
            }
        ).encode("utf-8")
        headers: Sequence[tuple[str, bytes | str | None]] = []

    async def fake_get_task_callback_url(*_: object, **__: object) -> str | None:
        return "https://example.test/webhook"

    monkeypatch.setattr(
        webhook_dispatcher,
        "get_task_callback_url",
        fake_get_task_callback_url,
    )

    with pytest.raises(ValueError, match="event_id header is required"):
        await webhook_dispatcher.process_message(
            db_pool=cast(Any, object()),
            http_client=cast(Any, FakeHttpClient([200])),
            message=cast(Any, _MissingHeaderMessage()),
            max_attempts=3,
            initial_backoff_seconds=0.01,
            max_backoff_seconds=0.05,
        )


def test_process_polled_messages_commits_each_handled_message() -> None:
    consumer = FakeConsumer(
        records={
            object(): [
                FakeMessage(
                    topic="tasks.completed",
                    payload={
                        "task_id": "019c6db7-0857-7858-af93-f724ae4fe2c2",
                        "status": "COMPLETED",
                        "billing_state": "CAPTURED",
                        "result": {"sum": 5},
                    },
                ),
                FakeMessage(
                    topic="tasks.failed",
                    payload={
                        "task_id": "019c6db7-1439-7ace-bd2b-e1a3bb03328c",
                        "status": "FAILED",
                        "billing_state": "RELEASED",
                        "error": "boom",
                    },
                ),
            ]
        }
    )

    async def fake_process_message(*_: object, **__: object) -> bool:
        return True

    processed = webhook_dispatcher.process_polled_messages(
        consumer=consumer,
        db_pool=cast(Any, object()),
        http_client=FakeHttpClient([200, 200]),
        poll_timeout_ms=250,
        max_records=10,
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
        run_async=cast(Any, asyncio.run),
        process_message_fn=fake_process_message,
    )

    assert processed == 2
    assert consumer.poll_calls == [(250, 10)]
    assert consumer.commit_calls == 2


@pytest.mark.asyncio
async def test_process_polled_messages_async_returns_zero_when_no_messages() -> None:
    consumer = FakeConsumer(records={})

    processed = await webhook_dispatcher.process_polled_messages_async(
        consumer=consumer,
        db_pool=cast(Any, object()),
        http_client=cast(Any, FakeHttpClient([200])),
        poll_timeout_ms=250,
        max_records=10,
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )

    assert processed == 0
    assert consumer.commit_calls == 0


def test_build_redpanda_consumer_subscribes_to_terminal_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeKafkaConsumer(FakeConsumer):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(webhook_dispatcher, "KafkaConsumer", FakeKafkaConsumer)

    consumer = webhook_dispatcher.build_redpanda_consumer(_settings())

    typed_consumer = cast(FakeConsumer, consumer)
    assert captured["bootstrap_servers"] == ["redpanda:9092"]
    assert captured["group_id"] == "solution3-webhook-worker"
    assert captured["enable_auto_commit"] is False
    assert captured["auto_offset_reset"] == "latest"
    assert typed_consumer.subscribed_topics == [
        "tasks.completed",
        "tasks.failed",
        "tasks.cancelled",
        "tasks.expired",
    ]


def test_build_redpanda_consumer_rejects_empty_bootstrap_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeKafkaConsumer(FakeConsumer):
        def __init__(self, **kwargs: object) -> None:
            super().__init__()

    monkeypatch.setattr(webhook_dispatcher, "KafkaConsumer", FakeKafkaConsumer)

    with pytest.raises(RuntimeError, match="bootstrap servers are not configured"):
        webhook_dispatcher.build_redpanda_consumer(
            SimpleNamespace(
                redpanda_bootstrap_servers=" , ",
                redpanda_topic_task_completed="tasks.completed",
                redpanda_topic_task_failed="tasks.failed",
                redpanda_topic_task_cancelled="tasks.cancelled",
                redpanda_topic_task_expired="tasks.expired",
                webhook_delivery_timeout_seconds=3.0,
            )
        )


@pytest.mark.asyncio
async def test_main_async_processes_batch_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    metrics_ports: list[int] = []

    class FakePool:
        async def close(self) -> None:
            events.append(("pool_closed", {}))

    class FakeAsyncClient(FakeHttpClient):
        def __init__(self) -> None:
            super().__init__([200])

        async def aclose(self) -> None:
            events.append(("http_closed", {}))

    consumer = FakeConsumer()

    class FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

        def exception(self, event: str, **kwargs: object) -> None:
            events.append((event, dict(kwargs)))

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_process_polled_messages_async(*_: object, **__: object) -> int:
        stop_event.set()
        return 2

    stop_event = asyncio.Event()

    def fake_install_stop_handlers(received_stop_event: asyncio.Event) -> None:
        assert received_stop_event is stop_event

    monkeypatch.setattr(webhook_dispatcher, "logger", FakeLogger())
    monkeypatch.setattr(
        webhook_dispatcher,
        "load_settings",
        lambda: _settings_with_postgres(webhook_metrics_port=9500),
    )
    monkeypatch.setattr(
        "solution3.workers.webhook_dispatcher.asyncpg.create_pool", fake_create_pool
    )
    monkeypatch.setattr(webhook_dispatcher, "start_http_server", metrics_ports.append)
    monkeypatch.setattr(
        "solution3.workers.webhook_dispatcher.httpx.AsyncClient",
        lambda timeout: FakeAsyncClient(),
    )
    monkeypatch.setattr(webhook_dispatcher, "build_redpanda_consumer", lambda _settings: consumer)
    monkeypatch.setattr("solution3.workers.webhook_dispatcher.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(webhook_dispatcher, "_install_stop_handlers", fake_install_stop_handlers)
    monkeypatch.setattr(
        webhook_dispatcher,
        "process_polled_messages_async",
        fake_process_polled_messages_async,
    )

    await webhook_dispatcher._main_async(
        interval_seconds=0.1,
        poll_timeout_ms=250,
        max_records=10,
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )

    assert (
        "webhook_worker_started",
        {"interval_seconds": 0.1, "poll_timeout_ms": 250, "max_records": 10, "max_attempts": 3},
    ) in events
    assert metrics_ports == [9500]
    assert ("webhook_worker_batch_processed", {"count": 2}) in events
    assert ("http_closed", {}) in events
    assert ("pool_closed", {}) in events
    assert consumer.closed is True


@pytest.mark.asyncio
async def test_main_async_logs_iteration_failure_and_waits_for_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    metrics_ports: list[int] = []

    class FakePool:
        async def close(self) -> None:
            events.append("pool_closed")

    class FakeAsyncClient(FakeHttpClient):
        def __init__(self) -> None:
            super().__init__([200])

        async def aclose(self) -> None:
            events.append("http_closed")

    consumer = FakeConsumer()
    stop_event = asyncio.Event()
    wait_calls: list[float] = []

    class FakeLogger:
        def info(self, event: str, **_: object) -> None:
            events.append(event)

        def exception(self, event: str, **_: object) -> None:
            events.append(event)

    async def fake_create_pool(*_: object, **__: object) -> FakePool:
        return FakePool()

    async def fake_process_polled_messages_async(*_: object, **__: object) -> int:
        raise RuntimeError("boom")

    async def fake_wait_for(awaitable: Awaitable[bool], *, timeout: float) -> bool:
        wait_calls.append(timeout)
        stop_event.set()
        return await awaitable

    monkeypatch.setattr(webhook_dispatcher, "logger", FakeLogger())
    monkeypatch.setattr(
        webhook_dispatcher,
        "load_settings",
        lambda: _settings_with_postgres(webhook_metrics_port=9500),
    )
    monkeypatch.setattr(
        "solution3.workers.webhook_dispatcher.asyncpg.create_pool", fake_create_pool
    )
    monkeypatch.setattr(webhook_dispatcher, "start_http_server", metrics_ports.append)
    monkeypatch.setattr(
        "solution3.workers.webhook_dispatcher.httpx.AsyncClient",
        lambda timeout: FakeAsyncClient(),
    )
    monkeypatch.setattr(webhook_dispatcher, "build_redpanda_consumer", lambda _settings: consumer)
    monkeypatch.setattr("solution3.workers.webhook_dispatcher.asyncio.Event", lambda: stop_event)
    monkeypatch.setattr(webhook_dispatcher, "_install_stop_handlers", lambda _event: None)
    monkeypatch.setattr(
        webhook_dispatcher,
        "process_polled_messages_async",
        fake_process_polled_messages_async,
    )
    monkeypatch.setattr("solution3.workers.webhook_dispatcher.asyncio.wait_for", fake_wait_for)

    await webhook_dispatcher._main_async(
        interval_seconds=0.25,
        poll_timeout_ms=250,
        max_records=10,
        max_attempts=3,
        initial_backoff_seconds=0.01,
        max_backoff_seconds=0.05,
    )

    assert "webhook_worker_iteration_failed" in events
    assert metrics_ports == [9500]
    assert wait_calls == [0.25]
    assert "webhook_worker_stopped" in events
    assert "http_closed" in events
    assert "pool_closed" in events
    assert consumer.closed is True


def test_main_configures_logging_and_runs_async_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_calls: list[bool] = []
    async_calls: list[tuple[float, int, int, int, float, float]] = []

    def fake_parse_args() -> Any:
        return SimpleNamespace(
            interval=2.5,
            poll_timeout_ms=200,
            max_records=5,
            max_attempts=4,
            initial_backoff_seconds=0.2,
            max_backoff_seconds=6.0,
        )

    async def fake_main_async(
        *,
        interval_seconds: float,
        poll_timeout_ms: int,
        max_records: int,
        max_attempts: int,
        initial_backoff_seconds: float,
        max_backoff_seconds: float,
    ) -> None:
        async_calls.append(
            (
                interval_seconds,
                poll_timeout_ms,
                max_records,
                max_attempts,
                initial_backoff_seconds,
                max_backoff_seconds,
            )
        )

    def fake_asyncio_run(coro: object) -> None:
        assert asyncio.iscoroutine(coro)
        with pytest.raises(StopIteration):
            coro.send(None)

    monkeypatch.setattr(webhook_dispatcher, "_parse_args", fake_parse_args)
    monkeypatch.setattr(webhook_dispatcher, "_main_async", fake_main_async)
    monkeypatch.setattr(
        webhook_dispatcher,
        "configure_logging",
        lambda *, enable_sensitive: configure_calls.append(enable_sensitive),
    )
    monkeypatch.setattr("solution3.workers.webhook_dispatcher.asyncio.run", fake_asyncio_run)

    webhook_dispatcher.main()

    assert configure_calls == [False]
    assert async_calls == [(2.5, 200, 5, 4, 0.2, 6.0)]


def _settings_with_postgres(**overrides: object) -> Any:
    return SimpleNamespace(
        **{
            **_settings().__dict__,
            "postgres_dsn": "postgresql://postgres:postgres@postgres:5432/postgres",
            **overrides,
        }
    )
