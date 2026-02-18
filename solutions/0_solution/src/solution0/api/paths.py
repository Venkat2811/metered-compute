from __future__ import annotations

# System endpoints
HEALTH_PATH = "/health"
READY_PATH = "/ready"
METRICS_PATH = "/metrics"
HIT_PATH = "/hit"

# Task endpoints
COMPAT_TASK_SUBMIT_PATH = "/task"
V1_TASK_SUBMIT_PATH = "/v1/task"
COMPAT_TASK_POLL_PATH = "/poll"
V1_TASK_POLL_PATH = "/v1/poll"
COMPAT_TASK_CANCEL_PATH = "/task/{task_id}/cancel"
V1_TASK_CANCEL_PATH = "/v1/task/{task_id}/cancel"

# Admin endpoints
COMPAT_ADMIN_CREDITS_PATH = "/admin/credits"
V1_ADMIN_CREDITS_PATH = "/v1/admin/credits"
