from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests total",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
)

TOKEN_ISSUANCE_TOTAL = Counter(
    "token_issuance_total",
    "OAuth token issuance count",
    ["result"],
)

TASK_SUBMISSIONS_TOTAL = Counter(
    "task_submissions_total",
    "Task submission outcomes",
    ["result"],
)

TASK_COMPLETIONS_TOTAL = Counter(
    "task_completions_total",
    "Terminal task outcomes",
    ["status"],
)

TASKS_EXECUTED_TOTAL = Counter(
    "tasks_executed_total",
    "Worker execution outcomes",
    ["status"],
)

TASK_DURATION_SECONDS = Histogram(
    "task_duration_seconds",
    "Worker task execution duration",
    ["model_class"],
)

TASK_DISPATCHES_TOTAL = Counter(
    "task_dispatches_total",
    "Dispatcher publish outcomes",
    ["result"],
)

OUTBOX_EVENTS_PUBLISHED_TOTAL = Counter(
    "outbox_events_published_total",
    "Outbox events published to Redpanda",
    ["topic", "result"],
)

OUTBOX_PUBLISH_LAG_SECONDS = Gauge(
    "outbox_publish_lag_seconds",
    "Age in seconds of the oldest unpublished outbox event in the current relay batch",
)

EVENTS_PROJECTED_TOTAL = Counter(
    "events_projected_total",
    "Projection worker event outcomes",
    ["topic", "result"],
)

RECONCILER_RESOLUTIONS_TOTAL = Counter(
    "reconciler_resolutions_total",
    "Reconciler terminal repairs by resolution path",
    ["resolution", "status"],
)

WEBHOOK_DELIVERIES_TOTAL = Counter(
    "webhook_deliveries_total",
    "Webhook delivery outcomes",
    ["result"],
)

WEBHOOK_DELIVERY_DURATION_SECONDS = Histogram(
    "webhook_delivery_duration_seconds",
    "Webhook delivery duration by result",
    ["result"],
)
