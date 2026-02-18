from __future__ import annotations

from prometheus_client import Counter, Histogram

TASK_SUBMITTED = Counter("task_submitted_total", "Tasks submitted", ["status"])
TASK_COMPLETED = Counter("task_completed_total", "Tasks completed")
TASK_CANCELLED = Counter("task_cancelled_total", "Tasks cancelled")
TASK_FAILED = Counter("task_failed_total", "Tasks failed")

CREDIT_RESERVED = Counter("credit_reserved_total", "Credits reserved via TB")
CREDIT_CAPTURED = Counter("credit_captured_total", "Credits captured via TB")
CREDIT_RELEASED = Counter("credit_released_total", "Credits released via TB")
CREDIT_TOPUP = Counter("credit_topup_total", "Credits topped up via TB")

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Request duration",
    ["method", "endpoint", "status"],
)
