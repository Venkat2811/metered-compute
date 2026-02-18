"""OpenTelemetry tracing helpers for optional runtime instrumentation."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from threading import Lock

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind
from opentelemetry.util.types import AttributeValue

from solution1.core.settings import AppSettings

_TRACING_INIT_LOCK = Lock()
_TRACING_INITIALIZED = False


def sanitize_trace_context_carrier(carrier: Mapping[str, object] | None) -> dict[str, str]:
    """Return only valid string key/value pairs from an incoming propagation carrier."""
    if carrier is None:
        return {}
    sanitized: dict[str, str] = {}
    for key, value in carrier.items():
        if isinstance(key, str) and isinstance(value, str):
            sanitized[key] = value
    return sanitized


def inject_current_trace_context() -> dict[str, str]:
    """Inject the current span context into a carrier dictionary."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


def extract_trace_context(carrier: Mapping[str, object] | None) -> Context | None:
    """Extract a trace propagation context from the provided carrier."""
    sanitized = sanitize_trace_context_carrier(carrier)
    if not sanitized:
        return None
    return propagate.extract(sanitized)


@contextmanager
def start_span(
    *,
    tracer_name: str,
    span_name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, AttributeValue] | None = None,
    parent_carrier: Mapping[str, object] | None = None,
) -> Iterator[Span]:
    """Start a span with optional parent context and attributes."""
    tracer = trace.get_tracer(tracer_name)
    parent_context = extract_trace_context(parent_carrier)
    with tracer.start_as_current_span(
        span_name,
        context=parent_context,
        kind=kind,
    ) as span:
        if attributes is not None:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span


def configure_process_tracing(*, settings: AppSettings, service_name: str) -> bool:
    """Initialize process-level OpenTelemetry tracing if enabled."""
    if not bool(getattr(settings, "otel_enabled", False)):
        return False

    service_namespace = str(getattr(settings, "otel_service_namespace", "metered-compute"))
    app_env = str(getattr(settings, "app_env", "dev"))
    endpoint = str(
        getattr(
            settings,
            "otel_exporter_otlp_traces_endpoint",
            "http://otel-collector:4318/v1/traces",
        )
    )
    timeout_seconds = float(getattr(settings, "otel_export_timeout_seconds", 3.0))
    sampler_ratio = float(getattr(settings, "otel_sampler_ratio", 1.0))
    sampler_ratio = max(0.0, min(1.0, sampler_ratio))

    global _TRACING_INITIALIZED
    with _TRACING_INIT_LOCK:
        if _TRACING_INITIALIZED:
            return True

        current_provider = trace.get_tracer_provider()
        if isinstance(current_provider, TracerProvider):
            _TRACING_INITIALIZED = True
            return True

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.namespace": service_namespace,
                "deployment.environment": app_env,
            }
        )
        tracer_provider = TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(sampler_ratio),
        )
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            timeout=timeout_seconds,
        )
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(tracer_provider)
        _TRACING_INITIALIZED = True
    return True
