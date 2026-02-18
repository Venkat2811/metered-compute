#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

from solution2.api.paths import COMPAT_TASK_POLL_PATH, COMPAT_TASK_SUBMIT_PATH, V1_OAUTH_TOKEN_PATH
from solution2.core.defaults import DEFAULT_USER1_API_KEY

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_API_KEY = os.getenv("ALICE_API_KEY", DEFAULT_USER1_API_KEY)
RETRYABLE_ERROR_CODES = {"TOO_MANY_REQUESTS", "SERVICE_DEGRADED"}
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"}


def _oauth_token(client: httpx.Client, *, api_key: str) -> str:
    response = client.post(V1_OAUTH_TOKEN_PATH, json={"api_key": api_key})
    payload: dict[str, Any] = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"oauth token exchange failed: {payload}")
    token = payload.get("access_token")
    if not isinstance(token, str) or token.count(".") != 2:
        raise RuntimeError(f"unexpected token payload: {payload}")
    return token


def _submit_with_retry(
    client: httpx.Client,
    *,
    task_path: str,
    access_token: str,
    x: int,
    y: int,
    max_attempts: int,
    submit_backoff_seconds: float,
) -> tuple[str, dict[str, Any]]:
    for attempt in range(1, max_attempts + 1):
        response = client.post(
            task_path,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"x": x, "y": y},
        )
        payload: dict[str, Any] = response.json()
        print(f"submit[{attempt}]: {json.dumps(payload, separators=(',', ':'))}")

        task_id_raw = payload.get("task_id")
        if isinstance(task_id_raw, str) and task_id_raw:
            return task_id_raw, payload

        error_code = (
            payload.get("error", {}).get("code") if isinstance(payload.get("error"), dict) else None
        )
        if error_code not in RETRYABLE_ERROR_CODES:
            raise RuntimeError(f"non-retryable submit failure: {payload}")
        time.sleep(submit_backoff_seconds)

    raise RuntimeError("submit attempts exhausted")


def _poll_until_terminal(
    client: httpx.Client,
    *,
    poll_path: str,
    access_token: str,
    task_id: str,
    max_attempts: int,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    for attempt in range(1, max_attempts + 1):
        response = client.get(
            poll_path,
            params={"task_id": task_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        payload: dict[str, Any] = response.json()
        print(f"poll[{attempt}]: {json.dumps(payload, separators=(',', ':'))}")

        status = payload.get("status")
        if isinstance(status, str) and status in TERMINAL_STATUSES:
            return payload
        time.sleep(poll_interval_seconds)

    raise RuntimeError("poll attempts exhausted")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit one async task and poll until a terminal status is reached."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--task-path", default=COMPAT_TASK_SUBMIT_PATH)
    parser.add_argument("--poll-path", default=COMPAT_TASK_POLL_PATH)
    parser.add_argument("--x", type=int, default=5)
    parser.add_argument("--y", type=int, default=3)
    parser.add_argument("--submit-attempts", type=int, default=20)
    parser.add_argument("--poll-attempts", type=int, default=40)
    parser.add_argument("--submit-backoff-seconds", type=float, default=1.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        access_token = _oauth_token(client, api_key=args.api_key)
        task_id, _ = _submit_with_retry(
            client,
            task_path=args.task_path,
            access_token=access_token,
            x=args.x,
            y=args.y,
            max_attempts=args.submit_attempts,
            submit_backoff_seconds=args.submit_backoff_seconds,
        )
        terminal = _poll_until_terminal(
            client,
            poll_path=args.poll_path,
            access_token=access_token,
            task_id=task_id,
            max_attempts=args.poll_attempts,
            poll_interval_seconds=args.poll_interval_seconds,
        )

    status = terminal.get("status")
    if status != "COMPLETED":
        print(f"terminal status was not COMPLETED: {terminal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
