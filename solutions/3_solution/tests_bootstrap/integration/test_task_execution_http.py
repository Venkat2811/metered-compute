from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest

BASE_URL = "http://localhost:8000"
ALICE_API_KEY = "586f0ef6-e655-4413-ab08-a481db150389"
REPO_ROOT = Path(__file__).resolve().parents[2]


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


async def _wait_for_projected_result(
    *, task_id: str, timeout_seconds: float
) -> dict[str, object] | None:
    deadline = time.time() + timeout_seconds
    connection = await asyncpg.connect(dsn=_postgres_dsn())
    try:
        while time.time() < deadline:
            row = await connection.fetchrow(
                """
                SELECT status, billing_state, result
                FROM query.task_query_view
                WHERE task_id=$1::uuid
                """,
                task_id,
            )
            result_value = row["result"] if row is not None else None
            if isinstance(result_value, str):
                result_value = json.loads(result_value)
            if row is not None and row["status"] == "COMPLETED" and result_value is not None:
                return {
                    "status": row["status"],
                    "billing_state": row["billing_state"],
                    "result": result_value,
                }
            await asyncio.sleep(0.5)
        return None
    finally:
        await connection.close()


def _delete_task_cache_key(task_id: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", "DEL", f"task:{task_id}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


async def _reset_projection_state_in_postgres() -> None:
    connection = await asyncpg.connect(dsn=_postgres_dsn())
    try:
        await connection.execute("TRUNCATE query.task_query_view")
        await connection.execute(
            """
            DELETE FROM cmd.inbox_events
            WHERE consumer_name = ANY($1::text[])
            """,
            ["projector", "projector-rebuild"],
        )
        await connection.execute(
            """
            DELETE FROM cmd.projection_checkpoints
            WHERE projector_name = ANY($1::text[])
            """,
            ["projector", "projector-rebuild"],
        )
    finally:
        await connection.close()


async def _age_task_command(task_id: str, *, age_seconds: int) -> None:
    connection = await asyncpg.connect(dsn=_postgres_dsn())
    try:
        await connection.execute(
            """
            UPDATE cmd.task_commands
            SET created_at = now() - make_interval(secs => $2),
                updated_at = now() - make_interval(secs => $2)
            WHERE task_id = $1::uuid
            """,
            task_id,
            age_seconds,
        )
    finally:
        await connection.close()


@pytest.mark.integration
def test_submit_completes_over_live_worker_path() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-complete-{uuid.uuid4()}",
    }

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 2, "y": 3},
        timeout=10.0,
    )
    assert submit.status_code == 201, submit.text
    task_id = submit.json()["task_id"]

    deadline = time.time() + 30.0
    final_payload: dict[str, object] | None = None
    while time.time() < deadline:
        poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert poll.status_code == 200, poll.text
        payload = poll.json()
        if payload["status"] in {"COMPLETED", "FAILED"}:
            final_payload = payload
            break
        time.sleep(0.5)

    assert final_payload is not None
    assert final_payload["status"] == "COMPLETED"
    assert final_payload["billing_state"] == "CAPTURED"
    assert final_payload["error"] is None


@pytest.mark.integration
def test_poll_falls_back_to_projected_query_view_when_task_cache_is_missing() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-query-fallback-{uuid.uuid4()}",
    }

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 4, "y": 5},
        timeout=10.0,
    )
    assert submit.status_code == 201, submit.text
    task_id = submit.json()["task_id"]

    deadline = time.time() + 30.0
    while time.time() < deadline:
        poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert poll.status_code == 200, poll.text
        if poll.json()["status"] == "COMPLETED":
            break
        time.sleep(0.5)
    else:
        raise AssertionError("task did not complete before fallback check")

    projected = asyncio.run(_wait_for_projected_result(task_id=task_id, timeout_seconds=15.0))
    assert projected == {
        "status": "COMPLETED",
        "billing_state": "CAPTURED",
        "result": {"sum": 9},
    }

    _delete_task_cache_key(task_id)

    fallback_poll = httpx.get(
        f"{BASE_URL}/v1/poll",
        params={"task_id": task_id},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    assert fallback_poll.status_code == 200, fallback_poll.text
    payload = fallback_poll.json()
    assert payload["status"] == "COMPLETED"
    assert payload["billing_state"] == "CAPTURED"
    assert payload["result"] == {"sum": 9}


@pytest.mark.integration
def test_rebuilder_replays_redpanda_log_and_restores_projection_state() -> None:
    access_token = _oauth_access_token(api_key=ALICE_API_KEY)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Idempotency-Key": f"itest-rebuild-{uuid.uuid4()}",
    }

    submit = httpx.post(
        f"{BASE_URL}/v1/task",
        headers=headers,
        json={"x": 6, "y": 7},
        timeout=10.0,
    )
    assert submit.status_code == 201, submit.text
    task_id = submit.json()["task_id"]

    deadline = time.time() + 30.0
    while time.time() < deadline:
        poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert poll.status_code == 200, poll.text
        if poll.json()["status"] == "COMPLETED":
            break
        time.sleep(0.5)
    else:
        raise AssertionError("task did not complete before rebuild test")

    _compose("stop", "projector")
    try:
        asyncio.run(_reset_projection_state_in_postgres())
        _delete_task_cache_key(task_id)
        _compose(
            "run",
            "--rm",
            "--no-deps",
            "--build",
            "api",
            "python",
            "-m",
            "solution3.workers.rebuilder",
            "--from-beginning",
            "--poll-timeout-ms",
            "500",
            "--max-empty-polls",
            "4",
        )
        _delete_task_cache_key(task_id)
    finally:
        _compose("up", "-d", "projector")

    fallback_poll = httpx.get(
        f"{BASE_URL}/v1/poll",
        params={"task_id": task_id},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )
    assert fallback_poll.status_code == 200, fallback_poll.text
    payload = fallback_poll.json()
    assert payload["status"] == "COMPLETED"
    assert payload["billing_state"] == "CAPTURED"
    assert payload["result"] == {"sum": 13}


@pytest.mark.integration
def test_reconciler_expires_stale_reserved_task_when_worker_is_stopped() -> None:
    _compose("stop", "worker")
    try:
        access_token = _oauth_access_token(api_key=ALICE_API_KEY)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Idempotency-Key": f"itest-reconcile-{uuid.uuid4()}",
        }

        submit = httpx.post(
            f"{BASE_URL}/v1/task",
            headers=headers,
            json={"x": 8, "y": 9},
            timeout=10.0,
        )
        assert submit.status_code == 201, submit.text
        task_id = submit.json()["task_id"]

        pending_poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert pending_poll.status_code == 200, pending_poll.text
        assert pending_poll.json()["status"] == "PENDING"

        asyncio.run(_age_task_command(task_id, age_seconds=3600))
        _compose(
            "run",
            "--rm",
            "--no-deps",
            "--build",
            "api",
            "python",
            "-m",
            "solution3.workers.reconciler",
            "--once",
            "--stale-after-seconds",
            "60",
        )

        expired_poll = httpx.get(
            f"{BASE_URL}/v1/poll",
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        assert expired_poll.status_code == 200, expired_poll.text
        payload = expired_poll.json()
        assert payload["status"] == "EXPIRED"
        assert payload["billing_state"] == "EXPIRED"
    finally:
        _compose("up", "-d", "worker")
