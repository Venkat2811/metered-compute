"""Client-side gateway for external compute worker."""

from __future__ import annotations

import json
from typing import Any

import httpx


class ComputeError(RuntimeError):
    """Raised when the worker returns an unexpected response."""


def _coerce_compute_payload(payload: dict[str, Any]) -> dict[str, int]:
    """Validate and normalize compute payload from worker."""
    required = ("sum", "product")
    for key in required:
        value = payload.get(key)
        if not isinstance(value, int):
            raise ComputeError(f"compute response missing integer field {key!r}")
    return {"sum": payload["sum"], "product": payload["product"]}


def request_compute_sync(
    *,
    task_id: str,
    x: int,
    y: int,
    base_url: str,
    timeout_seconds: float,
    retry_attempts: int = 1,
) -> dict[str, int]:
    """Send a compute request to external worker and return deterministic result.

    The function is intentionally synchronous because it is executed inside
    `restate.Context.run(...)` as a durable side-effect boundary.
    """
    request_payload = {"task_id": task_id, "x": x, "y": y}
    url = f"{base_url.rstrip('/')}/compute"
    last_error: Exception | None = None

    for _ in range(max(1, retry_attempts)):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
                response = client.post(
                    url,
                    json=request_payload,
                    headers={"content-type": "application/json"},
                )
                if response.status_code != 200:
                    raise ComputeError(f"compute worker error: status={response.status_code}")

                body = json.loads(response.text)
                if not isinstance(body, dict):
                    raise ComputeError("compute worker response must be a JSON object")

                result = body.get("result")
                if not isinstance(result, dict):
                    raise ComputeError("compute worker response missing result payload")

                return _coerce_compute_payload(result)
        except (httpx.TimeoutException, httpx.RequestError, ComputeError) as exc:
            last_error = exc
            continue

    if last_error is None:
        raise RuntimeError("compute request failed without captured error")

    raise last_error
