# RFC-0000: Implementation Details

Parent: [README](./README.md)

## Schema

Four Postgres tables via sequential migrations (`db/migrations/0001-0005`):

- **users**: `api_key` (PK), `user_id` (UUID unique), `name`, `credits` (CHECK >= 0), `role` (admin/user), `created_at`, `updated_at`
- **tasks**: `task_id` (UUID PK), `api_key`/`user_id` (FK), `x`, `y`, `cost`, `status` (enum CHECK), `result` (JSONB), `error`, `runtime_ms`, `idempotency_key`, timestamps
- **credit_transactions**: `txn_id` (UUID PK), `user_id` (FK), `task_id`, `delta`, `reason`, `created_at`
- **credit_snapshots**: `user_id` (PK), `balance`, `snapshot_at`

### Task ID generation

All task IDs are UUIDv7 (RFC 9562) generated application-side via `uuid7()`. Time-ordered UUIDs eliminate B-tree random page splits on `tasks(task_id)` — inserts append sequentially. `ORDER BY task_id` = `ORDER BY created_at` implicitly. Internal IDs (`user_id`, `txn_id`) retain `gen_random_uuid()`.

### Index strategy

- `ux_tasks_user_idempotency_key` unique on `(user_id, idempotency_key)` where not null
- `idx_tasks_status_created` on `tasks(status, created_at)`
- `idx_tasks_user_created` on `tasks(user_id, created_at DESC)`
- `idx_tasks_running_started_at` on `(started_at)` where `status='RUNNING'`
- `idx_credit_txn_user_created` on `credit_transactions(user_id, created_at DESC)`

### Retention

- Redis task results: 24h TTL
- Redis idempotency keys: configurable TTL (passed as Lua ARGV)
- `tasks`: 90 days online
- `credit_transactions`: 365 days online

## Key metrics

| Metric                                       | Type      |
| -------------------------------------------- | --------- |
| `http_requests_total{method,path,status}`    | counter   |
| `http_request_duration_seconds{method,path}` | histogram |
| `task_submissions_total{result}`             | counter   |
| `task_completions_total{status}`             | counter   |
| `credit_deductions_total{reason}`            | counter   |
| `credit_lua_duration_seconds{result}`        | histogram |
| `celery_queue_depth`                         | gauge     |
| `reaper_refunds_total{reason}`               | counter   |
| `auth_cache_results_total{result}`           | counter   |
| `auth_db_lookups_total{result}`              | counter   |

## Key alerts (monitoring/prometheus/alerts.yml)

| Alert                      | Condition                            | Severity |
| -------------------------- | ------------------------------------ | -------- |
| Solution0ApiUnavailable    | `up{job="api"} == 0` for 1m         | critical |
| Solution0WorkerUnavailable | `up{job="worker"} == 0` for 2m      | critical |
| Solution0Api5xxSpike       | submit 5xx rate > 0.05 req/s for 5m | warning  |
| Solution0QueueDepthHigh    | `celery_queue_depth > 50` for 10m   | warning  |

## Dual-write gap details

Every post-admission flow (worker completion, failure refund, cancel, admin credits) writes to Postgres first, then updates Redis. If the Redis write fails after PG commit, the system enters an inconsistent state:

- **Worker completion**: PG marks COMPLETED, but Redis result cache not populated. Poll sees stale state until reaper cycle.
- **Worker failure refund**: PG records refund, but Redis credit balance not incremented. User sees stale (lower) balance.
- **Cancel refund**: Same pattern — PG refund committed, Redis balance stale.
- **Admin credits**: PG updated, returns 200, but Redis cache not synced. Silent failure.
- **Reaper stuck task**: PG marks FAILED + refund, but Redis active counter not decremented. User blocked on concurrency.

These gaps are bounded (reaper reconciles within 30-60s) and the safety invariant holds (under-charge, not over-charge). But they are real, and the reaper is load-bearing — if it's down, inconsistency accumulates.

### Hardening applied

To narrow the dual-write window, all post-PG Redis operations use retry-with-backoff (3 attempts, exponential backoff with jitter). Exhausted retries are surfaced through structured error logs and picked up by fault-test evidence collection. Additional hardening:

- **Query timeouts**: PG `statement_timeout` (50ms hot-path, 2s batch) kills rogue queries server-side. asyncpg `command_timeout` (100ms) as client-side backup. `idle_in_transaction_session_timeout` (500ms) prevents leaked transactions holding locks.
- **Redis timeouts**: `socket_timeout` and `socket_connect_timeout` (50ms) on all production connections. A hung Redis cannot block the event loop.
- **Retry jitter**: All exponential backoff includes `uniform(0.5, 1.5)` jitter to prevent thundering herds after transient failures.
- **Config validation**: `task_cost > 0`, `max_concurrent > 0` (prevents config-driven outages).
- **Input bounds**: `x`, `y` within INT32 range; API keys never in Redis pending markers or logged in plaintext.

## Internal contracts

Celery task payload: `task_id, x, y, cost, user_id, api_key, trace_id`
