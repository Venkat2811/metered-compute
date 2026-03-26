"""Client-side gateway for external compute worker."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx


class ComputeError(RuntimeError):
    """Raised when the worker returns an unexpected response."""


class ComputeTimeoutError(ComputeError):
    """Raised when the compute request exceeds the configured timeout budget."""


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
    user_id: str,
    x: int,
    y: int,
    model_class: str,
    base_url: str,
    timeout_seconds: float,
    retry_attempts: int = 1,
) -> dict[str, int]:
    """Send a compute request to external worker and return deterministic result.

    The function is intentionally synchronous because it is executed inside
    `restate.Context.run(...)` as a durable side-effect boundary.
    """
    request_payload = {
        "task_id": task_id,
        "user_id": user_id,
        "x": x,
        "y": y,
        "model_class": model_class,
    }
    url = f"{base_url.rstrip('/')}/compute"
    end_time = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    response: httpx.Response | None = None

    for _ in range(max(1, retry_attempts)):
        remaining_timeout = max(0.0, end_time - time.monotonic())
        if remaining_timeout <= 0:
            raise ComputeTimeoutError("compute request budget exhausted")
        try:
            with httpx.Client(timeout=httpx.Timeout(remaining_timeout)) as client:
                response = client.post(
                    url,
                    json=request_payload,
                    headers={"content-type": "application/json"},
                )
                status_code = response.status_code
                if status_code == 200:
                    body = json.loads(response.text)
                    if not isinstance(body, dict):
                        raise ComputeError("compute worker response must be a JSON object")

                    result = body.get("result")
                    if not isinstance(result, dict):
                        raise ComputeError("compute worker response missing result payload")

                    return _coerce_compute_payload(result)

                if status_code >= 500:
                    raise ComputeError(f"compute worker internal error: status={status_code}")

                raise ComputeError(f"compute worker error: status={status_code}")
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if isinstance(exc, httpx.TimeoutException):
                last_error = ComputeTimeoutError("compute request timed out")
            else:
                # treat generic transport issues as transient as they are often temporary
                last_error = exc
            continue
        except ComputeError as exc:
            last_error = exc
            if response is not None and response.status_code >= 500:
                continue
            raise
        except json.JSONDecodeError as exc:
            last_error = ComputeError(f"compute worker response not valid JSON: {exc}")
            raise

    if last_error is None:
        raise RuntimeError("compute request failed without captured error")

    raise last_error
