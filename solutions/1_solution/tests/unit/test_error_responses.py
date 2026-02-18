"""Unit tests for shared API error-response helpers."""

from __future__ import annotations

import json

from solution1.api.error_responses import api_error_response


def test_api_error_response_matches_contract() -> None:
    """Ensure canonical error payload matches the public error envelope contract."""
    response = api_error_response(
        status_code=503,
        code="SERVICE_DEGRADED",
        message="Service temporarily unavailable",
    )

    assert response.status_code == 503
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"]["code"] == "SERVICE_DEGRADED"
    assert payload["error"]["message"] == "Service temporarily unavailable"
    assert payload["error"]["retry_after"] is None


def test_api_error_response_includes_retry_after_when_set() -> None:
    """Validate optional retry-after metadata is preserved on the envelope."""
    response = api_error_response(
        status_code=429,
        code="TOO_MANY_REQUESTS",
        message="max concurrency",
        retry_after=10,
    )

    assert response.status_code == 429
    payload = json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"]["retry_after"] == 10
