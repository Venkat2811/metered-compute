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

TASK_SUBMISSIONS_TOTAL = Counter(
    "task_submissions_total",
    "Task submissions by result",
    ["result"],
)

TASK_COMPLETIONS_TOTAL = Counter(
    "task_completions_total",
    "Task completion outcomes",
    ["status"],
)

CREDIT_DEDUCTIONS_TOTAL = Counter(
    "credit_deductions_total",
    "Credit mutation count",
    ["reason"],
)

CREDIT_LUA_DURATION_SECONDS = Histogram(
    "credit_lua_duration_seconds",
    "Redis Lua admission latency",
    ["result"],
)

CELERY_QUEUE_DEPTH = Gauge(
    "celery_queue_depth",
    "Approximate Celery queue depth",
)

REAPER_REFUNDS_TOTAL = Counter(
    "reaper_refunds_total",
    "Refund count applied by reaper",
    ["reason"],
)

AUTH_CACHE_RESULTS_TOTAL = Counter(
    "auth_cache_results_total",
    "Authentication cache lookup outcomes",
    ["result"],
)

AUTH_DB_LOOKUPS_TOTAL = Counter(
    "auth_db_lookups_total",
    "Authentication database lookup outcomes",
    ["result"],
)
