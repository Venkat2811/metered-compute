from __future__ import annotations

import asyncio
import json
import os
import queue
import socket
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import asyncpg
import httpx
import pytest

BASE_URL = "http://localhost:8000"
ALICE_API_KEY = "586f0ef6-e655-4413-ab08-a481db150389"


def _oauth_access_token(*, api_key: str) -> str:
    response = httpx.post(
        f"{BASE_URL}/v1/oauth/token",
        json={"api_key": api_key},
        timeout=10.0,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _postgres_dsn() -> str:
    return os.environ.get(
        "SOLUTION3_TEST_POSTGRES_DSN",
        "postgresql://postgres:postgres@localhost:5432/postgres",
    )


def _find_unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


class _CallbackServer:
    def __init__(self) -> None:
        self.port = _find_unused_port()
        self.requests: queue.Queue[dict[str, Any]] = queue.Queue()

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                parent.requests.put(
                    {
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "body": json.loads(raw_body.decode("utf-8")),
                    }
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _CallbackServer:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)

    def callback_url(self) -> str:
        return f"http://host.docker.internal:{self.port}/callback"

    def wait_for_request(self, *, timeout_seconds: float) -> dict[str, Any]:
        return self.requests.get(timeout=timeout_seconds)


def _wait_for_terminal_task(
    *, access_token: str, task_id: str, timeout_seconds: float
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert isinstance(payload, dict)
        if payload["status"] in {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}:
            return cast(dict[str, Any], payload)
        time.sleep(0.5)
    raise AssertionError(f"task {task_id} did not reach terminal state in time")


async def _wait_for_dead_letter(*, task_id: str, timeout_seconds: float) -> dict[str, Any] | None:
    deadline = time.time() + timeout_seconds
    connection = await asyncpg.connect(dsn=_postgres_dsn())
    try:
        while time.time() < deadline:
            row = await connection.fetchrow(
                """
                SELECT topic, callback_url, attempts, last_error, created_at
                FROM cmd.webhook_dead_letters
                WHERE task_id = $1::uuid
                """,
                task_id,
            )
            if row is not None:
                created_at = row["created_at"]
                return {
                    "topic": row["topic"],
                    "callback_url": row["callback_url"],
                    "attempts": row["attempts"],
                    "last_error": row["last_error"],
                    "created_at": created_at.isoformat()
                    if isinstance(created_at, datetime)
                    else None,
                }
            await asyncio.sleep(0.5)
        return None
    finally:
        await connection.close()


@pytest.mark.integration
def test_webhook_worker_delivers_terminal_callback_to_registered_url() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-webhook-ok-{uuid.uuid4()}",
    }

    with _CallbackServer() as server:
        submit = httpx.post(
            f"{BASE_URL}/v1/task",
            headers=headers,
            json={"x": 8, "y": 13, "callback_url": server.callback_url()},
            timeout=10.0,
        )
        assert submit.status_code == 201, submit.text
        task_id = str(submit.json()["task_id"])

        final_payload = _wait_for_terminal_task(
            access_token=access_token,
            task_id=task_id,
            timeout_seconds=30.0,
        )
        assert final_payload["status"] == "COMPLETED"

        callback = server.wait_for_request(timeout_seconds=30.0)
        assert callback["path"] == "/callback"
        assert callback["body"] == {
            "task_id": task_id,
            "status": "COMPLETED",
            "billing_state": "CAPTURED",
            "result": {"sum": 21},
            "error": None,
        }
        assert callback["headers"]["X-Webhook-Event-Id"]


@pytest.mark.integration
def test_webhook_worker_dead_letters_after_repeated_delivery_failures() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-webhook-dlq-{uuid.uuid4()}",
    }
    unused_port = _find_unused_port()

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 3, "y": 4, "callback_url": f"http://host.docker.internal:{unused_port}/fail"},
        timeout=10.0,
    )
    assert submit.status_code == 201, submit.text
    task_id = str(submit.json()["task_id"])

    final_payload = _wait_for_terminal_task(
        access_token=access_token,
        task_id=task_id,
        timeout_seconds=30.0,
    )
    assert final_payload["status"] == "COMPLETED"

    dead_letter = asyncio.run(_wait_for_dead_letter(task_id=task_id, timeout_seconds=30.0))
    assert dead_letter is not None
    assert dead_letter["topic"] == "tasks.completed"
    assert dead_letter["callback_url"] == f"http://host.docker.internal:{unused_port}/fail"
    assert dead_letter["attempts"] == 3
    assert dead_letter["last_error"]
