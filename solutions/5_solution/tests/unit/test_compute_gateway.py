from __future__ import annotations

from typing import Any

import httpx
import pytest

from solution5.workers.compute_gateway import ComputeError, request_compute_sync


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.text = httpx.Response(200, json=payload).text


class _FakeClient:
    def __init__(self, responses: list[object | Exception]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, int], dict[str, str]]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        self.calls.append((url, json, headers))
        next_response = self._responses.pop(0)
        if isinstance(next_response, Exception):
            raise next_response
        assert isinstance(next_response, _FakeResponse)
        return next_response


def test_request_compute_sync_returns_sum_and_product(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"result": {"sum": 7, "product": 42}}

    client = _FakeClient([_FakeResponse(200, payload)])
    monkeypatch.setattr(httpx, "Client", lambda *_args, **_kwargs: client)

    result = request_compute_sync(task_id="task", x=3, y=4, base_url="http://compute", timeout_seconds=1.0)

    assert result == {"sum": 7, "product": 42}
    assert client.calls[0][0] == "http://compute/compute"
    assert client.calls[0][1] == {"task_id": "task", "x": 3, "y": 4}


def test_request_compute_sync_retries_after_transient_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        [
            _FakeResponse(500, {"error": "boom"}),
            _FakeResponse(200, {"result": {"sum": 5, "product": 6}}),
        ]
    )
    monkeypatch.setattr(httpx, "Client", lambda *_args, **_kwargs: client)

    result = request_compute_sync(
        task_id="task",
        x=1,
        y=2,
        base_url="http://compute",
        timeout_seconds=1.0,
        retry_attempts=2,
    )

    assert result == {"sum": 5, "product": 6}
    assert len(client.calls) == 2


def test_request_compute_sync_raises_if_payload_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([_FakeResponse(200, {"result": {"sum": "bad", "product": 5}})])
    monkeypatch.setattr(httpx, "Client", lambda *_args, **_kwargs: client)

    with pytest.raises(ComputeError):
        request_compute_sync(task_id="task", x=1, y=2, base_url="http://compute", timeout_seconds=1.0)


def test_request_compute_sync_gives_up_after_all_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([httpx.ReadTimeout("timeout"), httpx.ReadTimeout("timeout")])
    monkeypatch.setattr(httpx, "Client", lambda *_args, **_kwargs: client)

    with pytest.raises(httpx.ReadTimeout):
        request_compute_sync(task_id="task", x=1, y=2, base_url="http://compute", timeout_seconds=0.1, retry_attempts=2)

    assert len(client.calls) == 2
