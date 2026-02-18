from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import solution2.observability.tracing as tracing


def test_sanitize_trace_context_carrier_keeps_only_string_pairs() -> None:
    carrier = {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "rojo=00f067aa0ba902b7",
        "bad_value": 123,
    }

    sanitized = tracing.sanitize_trace_context_carrier(carrier)

    assert sanitized == {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "rojo=00f067aa0ba902b7",
    }


def test_inject_current_trace_context_uses_propagator(
    monkeypatch: Any,
) -> None:
    injected: dict[str, str] = {}

    def fake_inject(carrier: dict[str, str]) -> None:
        carrier["traceparent"] = "00-abc-def-01"
        injected.update(carrier)

    monkeypatch.setattr("solution2.observability.tracing.propagate.inject", fake_inject)

    carrier = tracing.inject_current_trace_context()

    assert carrier == {"traceparent": "00-abc-def-01"}
    assert injected == {"traceparent": "00-abc-def-01"}


def test_extract_trace_context_returns_none_when_invalid() -> None:
    assert tracing.extract_trace_context({"bad": 1}) is None


def test_configure_process_tracing_is_noop_when_disabled() -> None:
    settings = SimpleNamespace(
        otel_enabled=False,
        otel_service_namespace="mc-solution2",
        app_env="dev",
        otel_exporter_otlp_traces_endpoint="http://otel-collector:4318/v1/traces",
        otel_export_timeout_seconds=3.0,
        otel_sampler_ratio=1.0,
    )

    enabled = tracing.configure_process_tracing(
        settings=cast(Any, settings),
        service_name="solution2-test",
    )

    assert enabled is False
