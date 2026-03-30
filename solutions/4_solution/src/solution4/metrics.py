from __future__ import annotations

from prometheus_client import Counter, Histogram

TASK_SUBMITTED = Counter("task_submitted_total", "Tasks submitted", ["status"])
TASK_COMPLETED = Counter("task_completed_total", "Tasks completed")
TASK_CANCELLED = Counter("task_cancelled_total", "Tasks cancelled")
TASK_FAILED = Counter("task_failed_total", "Tasks failed")
TASK_TIMEOUT = Counter("task_timeout_total", "Tasks failed due to compute timeout")

COMPUTE_REQUESTS = Counter(
    "compute_requests_total",
    "Compute worker requests",
    ["result"],
)
COMPUTE_LATENCY_SECONDS = Histogram(
    "compute_request_seconds",
    "Compute worker request latency",
)

CREDIT_RESERVED = Counter("credit_reserved_total", "Credits reserved via TB")
CREDIT_CAPTURED = Counter("credit_captured_total", "Credits captured via TB")
CREDIT_RELEASED = Counter("credit_released_total", "Credits released via TB")
CREDIT_TOPUP = Counter("credit_topup_total", "Credits topped up via TB")

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Request duration",
    ["method", "endpoint", "status"],
)
