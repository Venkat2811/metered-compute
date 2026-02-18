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

TASKS_EXECUTED_TOTAL = Counter(
    "tasks_executed_total",
    "Worker task execution outcomes",
    ["status", "queue"],
)

TASK_DURATION_SECONDS = Histogram(
    "task_duration_seconds",
    "Worker task execution duration",
    ["model_class"],
)

TASK_FAILURES_TOTAL = Counter(
    "task_failures_total",
    "Worker task failures by reason",
    ["reason"],
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

STREAM_QUEUE_DEPTH = Gauge(
    "stream_queue_depth",
    "Approximate Redis stream queue depth",
)

REAPER_REFUNDS_TOTAL = Counter(
    "reaper_refunds_total",
    "Refund count applied by reaper",
    ["reason"],
)

REAPER_DRIFT_AUDITS_TOTAL = Counter(
    "reaper_drift_audits_total",
    "Credit drift audit outcomes",
    ["result"],
)

REAPER_DRIFT_ABS = Histogram(
    "reaper_drift_abs",
    "Absolute drift size observed by reaper",
)

REAPER_RETENTION_DELETES_TOTAL = Counter(
    "reaper_retention_deletes_total",
    "Rows deleted by reaper retention jobs",
    ["table"],
)

STREAM_CHECKPOINT_UPDATES_TOTAL = Counter(
    "stream_checkpoint_updates_total",
    "Stream checkpoint persistence outcomes",
    ["result"],
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

STREAM_CONSUMER_LAG = Gauge(
    "stream_consumer_lag",
    "Approximate lag for stream consumer group",
    ["group"],
)

STREAM_PENDING_ENTRIES = Gauge(
    "stream_pending_entries",
    "Pending entries in stream consumer group PEL",
    ["group"],
)

JWT_VALIDATION_DURATION_SECONDS = Histogram(
    "jwt_validation_duration_seconds",
    "JWT validation duration",
    ["result"],
)

SNAPSHOT_FLUSH_DURATION_SECONDS = Histogram(
    "snapshot_flush_duration_seconds",
    "Duration of reaper snapshot flush step",
)

TOKEN_ISSUANCE_TOTAL = Counter(
    "token_issuance_total",
    "OAuth token issuance count",
    ["grant_type"],
)

TOKEN_REVOCATIONS_TOTAL = Counter(
    "token_revocations_total",
    "Token revocation operations completed",
)

REVOCATION_PG_FALLBACK_TOTAL = Counter(
    "revocation_pg_fallback_total",
    "Revocation checks that used Postgres fallback due to Redis errors",
)

REVOCATION_CHECK_DURATION_SECONDS = Histogram(
    "revocation_check_duration_seconds",
    "Revocation check duration by backing source",
    ["source"],
)

PEL_RECOVERY_TOTAL = Counter(
    "pel_recovery_total",
    "Recovered stream messages from pending entries list",
)

CREDIT_DRIFT_ABSOLUTE = Gauge(
    "credit_drift_absolute",
    "Absolute credit drift by user",
    ["user_id"],
)

SNAPSHOT_LAST_SUCCESS_UNIXTIME = Gauge(
    "snapshot_last_success_unixtime",
    "Unix timestamp of the last successful reaper snapshot flush",
)

WEBHOOK_DELIVERIES_TOTAL = Counter(
    "webhook_deliveries_total",
    "Webhook delivery outcomes",
    ["result"],
)

WEBHOOK_DELIVERY_DURATION_SECONDS = Histogram(
    "webhook_delivery_duration_seconds",
    "Webhook callback delivery duration",
    ["result"],
)

WEBHOOK_QUEUE_DEPTH = Gauge(
    "webhook_queue_depth",
    "Pending webhook queue depth",
)

WEBHOOK_SCHEDULED_DEPTH = Gauge(
    "webhook_scheduled_depth",
    "Scheduled retry webhook depth",
)

WEBHOOK_DLQ_DEPTH = Gauge(
    "webhook_dlq_depth",
    "Webhook dead-letter queue depth",
)

OUTBOX_PUBLISH_LAG_SECONDS = Gauge(
    "outbox_publish_lag_seconds",
    "Age in seconds of oldest unpublished outbox event",
)

EVENTS_PROJECTED_TOTAL = Counter(
    "events_projected_total",
    "Projection worker event processing outcomes",
    ["event_type", "result"],
)

PROJECTION_LAG_SECONDS = Histogram(
    "projection_lag_seconds",
    "Projection lag in seconds from event occurrence to projection commit",
    ["event_type"],
)

RESERVATIONS_EXPIRED_TOTAL = Counter(
    "reservations_expired_total",
    "Expired reservations released by watchdog",
)

CREDITS_RELEASED_TOTAL = Counter(
    "credits_released_total",
    "Credits returned by watchdog reservation releases",
)

REDIS_KEYS_CLEANED_TOTAL = Counter(
    "redis_keys_cleaned_total",
    "Expired Redis task/result keys removed by watchdog",
)

RESERVATIONS_ACTIVE_GAUGE = Gauge(
    "reservations_active_gauge",
    "Active credit reservations in RESERVED state",
)

RESERVATIONS_CAPTURED_TOTAL = Counter(
    "reservations_captured_total",
    "Reservations transitioned from RESERVED to CAPTURED",
)

RESERVATIONS_RELEASED_TOTAL = Counter(
    "reservations_released_total",
    "Reservations transitioned from RESERVED to RELEASED",
)

RABBITMQ_QUEUE_DEPTH = Gauge(
    "rabbitmq_queue_depth",
    "Approximate RabbitMQ queue depth by queue name",
    ["queue"],
)
