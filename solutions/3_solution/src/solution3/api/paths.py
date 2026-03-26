from __future__ import annotations

V1_OAUTH_TOKEN_PATH = "/v1/oauth/token"  # nosec B105 - route path, not a credential
V1_TASK_SUBMIT_PATH = "/v1/task"
V1_TASK_POLL_PATH = "/v1/poll"
V1_TASK_CANCEL_PATH = "/v1/task/{task_id}/cancel"
V1_ADMIN_CREDITS_PATH = "/v1/admin/credits"
