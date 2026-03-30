# Connection Pool and Lifecycle Policy (BK-010)

Date: 2026-02-15  
Scope: API, worker, and reaper database/redis clients

## Pool Sizing Baseline

### Local (compose default)

- API Postgres pool: `min=1`, `max=10`
- Worker Postgres pool: bounded to `max<=8`
- Reaper Postgres pool: bounded to `max<=8`
- Redis clients:
  - API: shared async client initialized once in lifespan
  - Worker: shared sync client per worker process
  - Reaper: shared async client for process lifetime

### Production Guidance (initial)

- API pool max: start `2 * vCPU` and tune with saturation metrics.
- Worker/reaper pool max: keep lower than API unless worker path introduces heavy DB usage.
- Command timeout: keep strict (`~5s`) and fail fast under exhaustion.

## Lifecycle Contract

1. Pools/clients are process-scoped, not request-scoped.
2. Startup:
   - API: create pool/client in lifespan.
   - Worker: bootstrap pool/client once per worker process.
   - Reaper: create pool/client at process start.
3. Shutdown:
   - API: close redis + pool in lifespan teardown.
   - Worker: close DB loop + redis client on worker process shutdown signal.
   - Reaper: close redis + pool in `finally` block.

## Saturation Behavior

- Pool acquisition failures on submit path are treated as controlled degradation (`503`) with compensation logic preserved.
- Admission gate still protects DB by rejecting overflow before write paths when possible.

## Operational Signals

Monitor:

- `http_requests_total{status="503",path="/v1/task"}`
- `http_request_duration_seconds` p95/p99
- DB connection count and wait events (`pg_stat_activity`)
- Queue depth (`celery_queue_depth`)

Escalation:

1. If sustained 503 from pool pressure:
   - increase API pool max conservatively
   - reduce per-user concurrency if DB is bottleneck
2. If DB saturation persists:
   - scale read/write capacity
   - revisit transaction + persistence strategy in later solution tracks
