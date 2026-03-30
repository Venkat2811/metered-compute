# RFC-0001: Implementation Details

Parent: [README](./README.md)

## Schema (new tables vs solution 0)

Hashed API keys (no plaintext storage):

```sql
CREATE TABLE api_keys (
  key_hash CHAR(64) PRIMARY KEY,
  key_prefix VARCHAR(16) NOT NULL,
  user_id UUID NOT NULL REFERENCES users(user_id),
  role VARCHAR(32) NOT NULL DEFAULT 'user',
  tier VARCHAR(32) NOT NULL DEFAULT 'free',
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);
```

Credit drift audit (new for solution 1):

```sql
CREATE TABLE credit_drift_audit (
  audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  redis_balance INT NOT NULL,
  db_balance INT NOT NULL,
  drift INT NOT NULL,
  action_taken VARCHAR(32),
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Token revocation blacklist (day-partitioned, JTI-only — full token not stored since JWT is cryptographically verified):

```sql
CREATE TABLE token_revocations (
  jti       TEXT        NOT NULL,
  user_id   UUID        NOT NULL,
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (jti, revoked_at)
) PARTITION BY RANGE (revoked_at);

-- pg_partman manages partition lifecycle (create future, drop expired):
SELECT partman.create_parent(
  p_parent_table := 'public.token_revocations',
  p_control := 'revoked_at',
  p_interval := '1 day',
  p_premake := 2
);
UPDATE partman.part_config
SET retention = '2 days', retention_keep_table = false
WHERE parent_table = 'public.token_revocations';
-- Result: all Tuesday revocations dropped Thursday morning.
-- DROP TABLE is instant — zero vacuum, no bloat.

CREATE INDEX idx_token_revocations_user
  ON token_revocations (user_id, revoked_at);
```

### Task ID generation

All task IDs are UUIDv7 (RFC 9562) generated application-side via `uuid7()` before passing to the Lua mega-script. Time-ordered UUIDs eliminate random B-tree page splits on PG snapshot writes and give implicit chronological ordering. Internal IDs (`audit_id`) retain `gen_random_uuid()`.

### Retention

- Redis task hashes: 24h TTL
- Redis idempotency keys: 24h TTL
- Redis revocation sets: 36h TTL (bucket TTL)
- Stream: 48h sliding window (`MAXLEN ~500000`)
- `token_revocations`: 2 days (partition drop — instant, zero vacuum)
- `credit_drift_audit`: 1 day default (`REAPER_CREDIT_DRIFT_AUDIT_RETENTION_SECONDS`, configurable)
- `credit_transactions`: 1 day default (`REAPER_CREDIT_TRANSACTION_RETENTION_SECONDS`, configurable)

### Redis key patterns

- `credits:{user_id}`
- `idem:{user_id}:{idempotency_key}`
- `active:{user_id}`
- `task:{task_id}`
- `result:{task_id}`
- `tasks:stream`
- `credits:dirty`
- `pending:{task_id}`
- `revoked:{user_id}:{YYYY-MM-DD}`

### Internal contracts

- Stream message fields: `task_id`, `payload`, `user_id`, `cost`
- Payload fields: `x`, `y`, `model_class`, `tier`, `trace_id`

---

## Code and pseudo-code

### Submit/admission path

```pseudo
require scope task:submit
validate idempotency key (trimmed, 1..128 chars if present)
model_class := payload.model_class or small
cost := task_cost_for_model(base_cost, model_class)
max_concurrent := max_concurrent_for_tier(base_max_concurrent, user.tier)

lua_result := EVALSHA admission_lua(
  credits_key, idem_key, active_key, stream_key, task_key,
  cost, task_id(uuid7), max_concurrent, payload_json, user_id, tier
)

if lua_result.reason == CACHE_MISS:
  hydrate credits from Postgres snapshot/users
  retry Lua once

if lua_result.reason == IDEMPOTENT:
  return existing task_id + estimated_seconds

if lua_result.ok:
  write pending marker in Redis
  persist task + credit transaction in Postgres
  delete pending marker
  return 201
else map reason -> 402/429/409/503
```

### Status/query path

```pseudo
require scope task:poll

if result:{task_id} exists and belongs to caller:
  return terminal result

if task:{task_id} exists and belongs to caller:
  if status is terminal and result missing:
    fallback to Postgres row
  else:
    return Redis task state with queue estimate

fallback to Postgres task row
```

### State mutation path

```pseudo
cancel:
  require scope task:cancel
  verify ownership
  guarded UPDATE tasks ... WHERE status IN (PENDING, RUNNING)
  if not updated: return 409
  insert credit transaction (+cost)
  Redis INCRBY credits, decrement active, update task hash, clear pending marker

worker:
  XREADGROUP or XAUTOCLAIM
  guarded PENDING->RUNNING
  execute model (10s one-time warmup, then model runtime)
  guarded RUNNING->COMPLETED (or ->FAILED with refund)
  update Redis task/result hashes
  decrement active
  XACK
```

### Actual Lua source (admission gate)

```lua
-- KEYS[1] = credits:{user_id}
-- KEYS[2] = idem:{user_id}:{key}
-- KEYS[3] = active:{user_id}
-- KEYS[4] = tasks:stream
-- KEYS[5] = task:{task_id}
-- ARGV[1] = cost
-- ARGV[2] = task_id
-- ARGV[3] = max_concurrent
-- ARGV[4] = idempotency_ttl_seconds
-- ARGV[5] = payload_json (for stream)
-- ARGV[6] = user_id
-- ARGV[7] = task_ttl_seconds
-- ARGV[8] = stream_maxlen_approx

-- 1. Idempotency
local existing = redis.call('GET', KEYS[2])
if existing then
  return cjson.encode({ok=false, reason='IDEMPOTENT', task_id=existing})
end
-- 2. Concurrency
local active = tonumber(redis.call('GET', KEYS[3]) or '0')
if active >= tonumber(ARGV[3]) then
  return cjson.encode({ok=false, reason='CONCURRENCY'})
end
-- 3. Credit check + deduct
local bal = tonumber(redis.call('GET', KEYS[1]))
if bal == nil then
  return cjson.encode({ok=false, reason='CACHE_MISS'})
end
if bal < tonumber(ARGV[1]) then
  return cjson.encode({ok=false, reason='INSUFFICIENT'})
end
redis.call('DECRBY', KEYS[1], ARGV[1])
-- 4. Enqueue to stream (bounded by approximate MAXLEN)
redis.call(
  'XADD', KEYS[4], 'MAXLEN', '~', tonumber(ARGV[8]), '*',
  'task_id', ARGV[2], 'payload', ARGV[5], 'user_id', ARGV[6], 'cost', ARGV[1]
)
-- 5. Write task status (read-your-writes for poll)
redis.call('HSET', KEYS[5], 'status', 'PENDING', 'user_id', ARGV[6],
           'cost', ARGV[1], 'created_at_epoch', tostring(redis.call('TIME')[1]))
redis.call('EXPIRE', KEYS[5], tonumber(ARGV[7]))
-- 6. Track
redis.call('SETEX', KEYS[2], ARGV[4], ARGV[2])
redis.call('INCR', KEYS[3])
redis.call('SADD', 'credits:dirty', KEYS[1])
return cjson.encode({ok=true, reason='OK'})
```

Key: deduct + enqueue + status write are ONE atomic operation within Redis. No dual-write gap.

---

## Key metrics (adds to solution 0)

| Metric                                       | Type      |
| -------------------------------------------- | --------- |
| `http_requests_total{method,path,status}`    | counter   |
| `http_request_duration_seconds{method,path}` | histogram |
| `task_submissions_total{result}`             | counter   |
| `task_completions_total{status}`             | counter   |
| `credit_deductions_total{reason}`            | counter   |
| `credit_lua_duration_seconds{result}`        | histogram |
| `stream_consumer_lag{group}`                 | gauge     |
| `stream_pending_entries{group}`              | gauge     |
| `jwt_validation_duration_seconds{result}`    | histogram |
| `credit_drift_absolute{user_id}`             | gauge     |
| `snapshot_flush_duration_seconds`            | histogram |
| `token_issuance_total{grant_type}`           | counter   |
| `pel_recovery_total`                         | counter   |
| `reaper_refunds_total{reason}`               | counter   |

## Key alerts (monitoring/prometheus/alerts.yml)

| Alert             | Condition                | Severity |
| ----------------- | ------------------------ | -------- |
| StreamConsumerLag | lag > 50 for 2 min       | warning  |
| StreamConsumerLag | lag > 200 for 2 min      | critical |
| DriftThreshold    | drift > 100 for any user | critical |
| SnapshotStale     | last snapshot > 10 min   | warning  |
| PELGrowing        | pending > 20 for 5 min   | warning  |

### Tracing

- `trace_id` propagated: API → stream message field → worker
- Full task lifecycle visible in Tempo via Grafana
- OTel Collector + Tempo available via `docker compose --profile tracing up -d`

---

## Dual-write gap details

The Redis Lua admission gate is atomic **within Redis**. But post-admission flows (worker completion, failure refund, cancel, admin credits) write to Postgres first, then update Redis. If the Redis write fails after PG commit, the system enters an inconsistent state:

- **Worker completion**: PG marks COMPLETED, but Redis result cache not populated. Poll sees stale state until reaper cycle.
- **Worker failure refund**: PG records refund, but Redis credit balance not incremented. User sees stale (lower) balance.
- **Cancel refund**: Same pattern — PG refund committed, Redis balance stale.
- **Admin credits**: PG updated, returns 200, but Redis cache not synced. Silent failure.
- **Reaper stuck task**: PG marks FAILED + refund, but Redis active counter not decremented. User blocked on concurrency.

### Hardening applied

To narrow the dual-write window, all post-PG Redis operations use retry-with-backoff (3 attempts, exponential backoff with jitter). Exhausted retries are surfaced in structured logs and reconciled by reaper recovery loops. Additional hardening:

- **Query timeouts**: PG `statement_timeout` (50ms hot-path, 2s batch) kills rogue queries server-side. asyncpg `command_timeout` (100ms) as client-side backup. `idle_in_transaction_session_timeout` (500ms) prevents leaked transactions holding locks.
- **Redis timeouts**: `socket_timeout` and `socket_connect_timeout` (50ms) on all production connections. A hung Redis cannot block the event loop.
- **Retry jitter**: All exponential backoff includes `uniform(0.5, 1.5)` jitter to prevent thundering herds after transient failures.
- **Revocation write order**: Redis cache write first (best-effort), then durable Postgres insert (source of truth).
- **Startup safety**: revocation rehydration failure blocks startup (fail-closed, not fail-open).
- **JWKS cache**: TTL cache keyed by JWKS URL with thread-safe lock and forced-refresh on key-miss.
- **Webhook queue**: `MAXLEN` enforced (prevents unbounded Redis memory growth).
- **Config validation**: `task_cost > 0`, `max_concurrent > 0` (prevents config-driven outages).
- **Input bounds**: `x`, `y` within INT32 range; API keys never in Redis pending markers.
