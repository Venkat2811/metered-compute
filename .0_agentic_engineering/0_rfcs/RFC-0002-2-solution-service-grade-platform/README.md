# RFC-0002: Service-Grade Platform - CQRS + RabbitMQ SLA Routing + Reservation Billing

- **Status:** Implemented
- **Owner:** Venkat
- **Date:** 2026-02-15
- **Solution slot:** `2_solution`
- **Deployment constraint:** Docker Compose only

## Context and scope

Solution 1 is fast but has two structural risks: Redis is a single point for both billing and queueing, and the dual-write between Redis and Postgres is eventually consistent. This solution makes a different tradeoff: Postgres is back on the command path (for transactional guarantees), but the command and query paths are fully separated, the dual-write problem is solved with the outbox pattern, and billing uses a reservation model that eliminates the need for refund jobs.

Common requirements source:

- `../../0_0_problem_statement_and_assumptions/README.md` (Sections A + B)
- `../../README.md` (solution matrix)

What changed vs solution 1:

- CQRS: command and query paths separated in code (separate routers, separate DB schemas), single API container
- RabbitMQ handles tiered queues (realtime/fast/batch) with DLQ
- Billing flow: reserve on submit, capture on success, release on failure/timeout
- Outbox/inbox pattern for reliable publish (dual-write solved)
- Redis is a query cache layer, not the billing authority
- Introduces request modes: async, sync, batch
- Webhook delivery via RabbitMQ

What this solution does NOT include:

- TigerBeetle (reservation is app-coordinated in Postgres)
- Replayable event log (RabbitMQ consumed = gone)
- Projection rebuild from event stream

### What this is and is not

This is **CQRS + outbox pattern**, not event sourcing.

- **CQRS**: Command and query paths are separated (different routers, schemas, data models). The command side writes to `cmd.*`, the query side reads from `query.*` + Redis cache.
- **Outbox pattern**: Reliable cross-system publish. Events are written to PG in the same transaction as command state, then relayed to RabbitMQ. This solves dual-write, not event sourcing.
- **NOT event sourcing**: The command table (`cmd.task_commands`) is the source of truth, mutated directly via `UPDATE`. Events are derived FROM state changes, not the other way around. You cannot rebuild command state from events. The query view can be rebuilt from the command table with a SQL join -- no event log needed.

### Matrix alignment (solution 2)

Code (implemented):

- CQRS command/query route separation with schema-level isolation
- RabbitMQ exchange + per-SLA tiered queues (realtime/fast/batch) + DLQ
- Reservation billing: reserve on submit, capture on success, release on failure/timeout
- Outbox/inbox pattern for reliable cross-system publish (dual-write solved)
- Task submit/poll/cancel + admin credits with tier/model-class/mode awareness
- Batch submission (up to 100 tasks per request)
- Webhook delivery via RabbitMQ exchange
- Watchdog: expired reservation release + result expiry
- Unit/integration/e2e/fault/scenario tests
- `structlog` JSON logs + Prometheus metrics + Grafana dashboards

Config (included, not full compose deployment):

- Prometheus + Alertmanager rules (`alertmanager.yml` included)
- OTel SDK + Collector + Tempo configs for distributed tracing
- OpenSearch for searchable logs (correlate by task_id, user_id, trace_id, error code)
- Grafana provisioned dashboards and datasources

Out of scope for solution 2 (described in later RFCs):

- TigerBeetle for financial-grade billing (solution 3)
- Redpanda for replayable event log with safe consumer lag (solution 3)
- Model-affinity dispatch / hot-cold routing (solution 3)
- Multi-region orchestration

## Goals and non-goals

### Goals

- Correct under all failure modes without refund/reaper jobs
- Independent scaling of read and write paths
- SLA-aware queue routing by tier and request mode
- Reliable cross-system publish via outbox

### Non-goals

- Event replay / projection rebuild from log
- Financial-ledger-grade invariants
- Multi-region active-active

## The actual design

### Design summary

- API: Single FastAPI process with command routes (write path) and query routes (read path) + OAuth service
- Queue: RabbitMQ with exchange + per-SLA queues + DLQ + webhook exchange
- Billing: reserve credits on submit (transactional), capture on success, release on failure/timeout
- Reliability: outbox relay publishes to RabbitMQ, inbox deduplicates at consumers, watchdog releases expired reservations
- DB: Single Postgres instance, `cmd` schema (tasks, reservations, outbox) + `query` schema (task_query_view)
- Cache: Redis for query-side task status cache and rate limit counters

Note: CQRS separation is in the code (separate routers, separate schemas, separate connection pools), not separate containers. This can be split into separate services later without architectural changes.

### Communication planes

```text
COMMAND PLANE (durable, correctness over latency)
  API -> PG reserve (txn) -> outbox -> relay -> RabbitMQ -> Worker
  Each hop: billing reservation, durable write, reliable publish, SLA routing

RESPONSE PLANE (direct, latency-optimized)
  Worker -> PG capture/release + Redis write-through -> Client polls Redis
  No queues in the response path. Result visible in Redis immediately.

PROJECTION PATH (background, through RabbitMQ)
  RabbitMQ -> Projector -> PG query view
  RabbitMQ -> Webhook worker -> HTTP POST
  Limitation: RabbitMQ consumed = gone. Consumer lag degrades RabbitMQ.
  If projector is down, messages accumulate in RabbitMQ -- not ideal for
  extended outages. Query view can always be rebuilt from command table via SQL.
```

The command and response planes are independent. A client sees the result as soon as the worker writes to Redis -- it does not wait for the projector to update the query view.

### Reservation state machine

```text
  RESERVED --> CAPTURED   (worker completes successfully)
  RESERVED --> RELEASED   (worker fails / timeout / user cancels)
```

Only these transitions are legal. Any other transition is a bug.
Credits are held (not spent) in RESERVED state. No refund logic needed.

### SLA routing table

| Tier           | sync                          | async          | batch       |
| -------------- | ----------------------------- | -------------- | ----------- |
| free           | rejected                      | queue.batch    | queue.batch |
| pro            | queue.fast (small model only) | queue.fast     | queue.batch |
| enterprise     | queue.realtime                | queue.realtime | queue.fast  |

When `queue.realtime` depth > threshold: spill to `queue.fast` with priority flag.
When any queue depth > hard limit: 503 + `Retry-After` header.

### System-context diagram

```text
Client
  |
  +-- POST /v1/oauth/token -------> OAuth Service
  |
  +-- POST /v1/task (JWT) ---------- command routes
  +-- POST /v1/task/{id}/cancel --- command routes
  +-- POST /v1/task/batch --------- command routes
  +-- POST /v1/admin/credits ------- command routes
  +-- GET  /v1/poll --------------- query routes
  |
  v
+---------------------------+
| API (FastAPI)             |
| cmd routes  | query routes|
+------+------+------+------+
       |             |
  +----+----+        +----------+
  |         |                   |
  v         v                   v
+--------+ +------------+  +--------+
|Postgres| | RabbitMQ   |  | Redis  |
|cmd.*   | | realtime Q |  | query  |
|query.* | | fast Q     |  | cache  |
|        | | batch Q    |  |rate lmt|
|        | | DLQ        |  +--------+
+---+----+ +-----+------+
    |             |
    | outbox      |
    | relay       |
    +----->       |
              +---v---------+
              | Worker      |
              | (subscribes |
              |  all queues)|
              +---+---------+
                  |
           +------v------+
           | Projector   |
           | updates     |
           | query schema|
           | + Redis     |
           +-------------+

+-----------+
| Watchdog  |  releases expired reservations + result expiry
+-----------+

+-----------+  +---------+
| Prometheus|  | Grafana |
+-----------+  +---------+
```

For detailed per-endpoint request flow diagrams showing exact store interactions, see [Request Flow Diagrams](./request-flows.md).

## APIs

### Public endpoints

`POST /v1/oauth/token` -- same as solution 1

`POST /v1/task` (JWT)

- Body: `{"x": 5, "y": 3, "model_class": "medium", "mode": "async", "callback_url": "https://..."}`
- `mode` optional (default: async). `callback_url` optional (webhook on completion).
- Success: `201 {"task_id": "...", "status": "PENDING", "queue": "fast", "expires_at": "..."}`

`POST /v1/task/batch` (JWT)

- Body: `{"tasks": [{"x": 1, "y": 2}, {"x": 3, "y": 4}, ...]}` (max 100)
- Success: `201 {"batch_id": "...", "task_ids": [...], "total_cost": 200}`

`GET /v1/poll?task_id=<uuid>` (JWT)

- Reads from query API (Redis cache -> Postgres query view fallback)
- Success: `200 {"task_id": "...", "status": "...", "result": ..., "expires_at": "..."}`
- Queue position and estimated time are not provided in solutions 2-3 (CQRS projections are eventually consistent; position would be stale by the time it's projected). Clients use `expires_at` for timeout decisions.
- Results transition to EXPIRED after 24h TTL via the watchdog (same pass as reservation expiry).

`POST /v1/task/{id}/cancel` (JWT)

- Releases reservation. Returns refunded credits.

`POST /v1/admin/credits` (admin JWT)

`GET /health`, `GET /ready`, `GET /metrics`

### Internal contracts

- RabbitMQ routing key: `tasks.<mode>.<tier>.<model_class>`
- Outbox event envelope: `event_id, task_id, user_id, tier, mode, model_class, cost, routing_key, schema_version`
- Inbox dedup key: `event_id`

## Data storage

### Stores and responsibilities

- Postgres (single instance, two schemas):
  - `cmd` schema: task_commands, credit_reservations, outbox_events, inbox_events, users, api_keys, credit_transactions
  - `query` schema: task_query_view
- Redis: query cache (task:{id} hashes), rate limit counters, concurrency counters
- RabbitMQ: task routing, retry, DLQ, webhook delivery

For the full data ownership model (which store owns what, source of truth per data type, consistency boundaries, and failure/recovery model), see [Data Ownership and Consistency](./data-ownership.md).

### Schema DDL

1. Task commands:

```sql
CREATE TABLE task_commands (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'async',
  model_class VARCHAR(16) NOT NULL DEFAULT 'small',
  status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
  x INT NOT NULL,
  y INT NOT NULL,
  cost INT NOT NULL,
  callback_url TEXT,
  idempotency_key VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

2. Credit reservations:

```sql
CREATE TABLE credit_reservations (
  reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID UNIQUE NOT NULL,
  user_id UUID NOT NULL,
  amount INT NOT NULL,
  state VARCHAR(16) NOT NULL DEFAULT 'RESERVED',
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

3. Outbox/inbox:

```sql
CREATE TABLE outbox_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregate_id UUID NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  routing_key VARCHAR(128) NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE inbox_events (
  event_id UUID PRIMARY KEY,
  consumer_name VARCHAR(64) NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

4. Query view:

```sql
CREATE TABLE task_query_view (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL,
  mode VARCHAR(16) NOT NULL,
  model_class VARCHAR(16) NOT NULL,
  status VARCHAR(24) NOT NULL,
  result JSONB,
  error TEXT,
  queue_name VARCHAR(32),
  runtime_ms INT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

### Task ID generation

All task IDs, batch IDs, and transfer IDs are UUIDv7 (RFC 9562) generated application-side via `uuid7()` (already shown in submit code above). Time-ordered UUIDs eliminate B-tree random page splits across all command tables -- `task_commands`, `credit_reservations`, `outbox_events`. The outbox relay benefits: `ORDER BY created_at` can be replaced with `ORDER BY event_id` when event IDs are also UUIDv7, since time ordering is implicit. Internal IDs (`reservation_id`, `inbox event_id`) retain `gen_random_uuid()` where time-ordering adds no value.

### Index strategy

- `ux_task_cmd_user_idem` unique on `(user_id, idempotency_key)` where key not null
- `idx_reservations_state_expires` on `(state, expires_at)`
- `idx_outbox_unpublished` on `(published_at)` where `published_at IS NULL`
- `idx_task_query_user_updated` on `(user_id, updated_at DESC)`
- `idx_credit_txn_user_created` on `(credit_transactions(user_id, created_at DESC))`

### Retention

- RabbitMQ DLQ: 7 days
- Redis query cache: 24h TTL
- Query view: 120 days online
- Reservations: 180 days
- Credit transactions: 365 days

## Code and pseudo-code

### Submit path (transactional outbox)

```python
async def submit_task(jwt_claims, payload, idem_key):
    tier = jwt_claims.tier
    mode = payload.get("mode", "async")
    model = payload.get("model_class", "small")
    cost = compute_cost(model, tier)
    queue = route_to_queue(tier, mode, model)

    async with cmd_db.transaction():
        # 1. Idempotency
        existing = await cmd_db.fetchval(
            "SELECT task_id FROM task_commands WHERE user_id=$1 AND idempotency_key=$2",
            jwt_claims.sub, idem_key)
        if existing:
            return {"task_id": str(existing)}

        # 2. Concurrency
        active = await cmd_db.fetchval(
            "SELECT COUNT(*) FROM credit_reservations WHERE user_id=$1 AND state='RESERVED'",
            jwt_claims.sub)
        if active >= get_max_concurrent(tier):
            raise HTTPException(429, "CONCURRENCY_LIMIT")

        # 3. Reserve credits (check row count to verify sufficient balance)
        task_id = uuid7()
        result = await cmd_db.execute(
            "UPDATE users SET credits=credits-$1 WHERE user_id=$2 AND credits>=$1",
            cost, jwt_claims.sub)
        if result == "UPDATE 0":
            raise HTTPException(402, "INSUFFICIENT_CREDITS")
        await cmd_db.execute("""
            INSERT INTO credit_reservations(task_id, user_id, amount, state, expires_at)
            VALUES($1, $2, $3, 'RESERVED', now() + interval '10 minutes')
        """, task_id, jwt_claims.sub, cost)

        # 4. Command row
        await cmd_db.execute("""
            INSERT INTO task_commands(task_id, user_id, tier, mode, model_class, x, y, cost, callback_url, idempotency_key)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        """, task_id, jwt_claims.sub, tier, mode, model, payload["x"], payload["y"], cost,
             payload.get("callback_url"), idem_key)

        # 5. Outbox (same transaction = atomic)
        await cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, routing_key, payload)
            VALUES($1, 'task.requested', $2, $3)
        """, task_id, queue, json.dumps({...}))

    # 6. Write-through to query cache
    await redis.hset(f"task:{task_id}", mapping={"status": "PENDING", "user_id": str(jwt_claims.sub)})
    await redis.expire(f"task:{task_id}", 86400)

    return {"task_id": str(task_id), "queue": queue}
```

### Outbox relay (polls, publishes to RabbitMQ)

```python
async def relay_outbox():
    rows = await cmd_db.fetch("""
        SELECT event_id, routing_key, payload FROM outbox_events
        WHERE published_at IS NULL ORDER BY created_at LIMIT 100
    """)
    for row in rows:
        channel.basic_publish(exchange="tasks", routing_key=row["routing_key"],
                              body=row["payload"], properties=pika.BasicProperties(delivery_mode=2))
        await cmd_db.execute("UPDATE outbox_events SET published_at=now() WHERE event_id=$1", row["event_id"])
```

### Worker completion (capture/release)

```python
def on_task_complete(task_id, success, result=None, error=None):
    with cmd_db.transaction():
        reservation = cmd_db.fetchrow(
            "SELECT reservation_id, user_id, amount FROM credit_reservations WHERE task_id=$1 AND state='RESERVED' FOR UPDATE",
            task_id)
        if success:
            cmd_db.execute("UPDATE credit_reservations SET state='CAPTURED', updated_at=now() WHERE task_id=$1", task_id)
            cmd_db.execute("INSERT INTO credit_transactions(user_id,task_id,delta,reason) VALUES($1,$2,$3,'capture')",
                           reservation["user_id"], task_id, -reservation["amount"])
            cmd_db.execute("UPDATE task_commands SET status='COMPLETED', updated_at=now() WHERE task_id=$1", task_id)
        else:
            cmd_db.execute("UPDATE credit_reservations SET state='RELEASED', updated_at=now() WHERE task_id=$1", task_id)
            cmd_db.execute("UPDATE users SET credits=credits+$1 WHERE user_id=$2",
                           reservation["amount"], reservation["user_id"])
            cmd_db.execute("INSERT INTO credit_transactions(user_id,task_id,delta,reason) VALUES($1,$2,$3,'release')",
                           reservation["user_id"], task_id, reservation["amount"])
            cmd_db.execute("UPDATE task_commands SET status='FAILED', updated_at=now() WHERE task_id=$1", task_id)

    # Update query model
    redis.hset(f"task:{task_id}", mapping={"status": "COMPLETED" if success else "FAILED", "result": str(result)})

    # Webhook delivery if callback_url exists
    task = cmd_db.fetchrow("SELECT callback_url FROM task_commands WHERE task_id=$1", task_id)
    if task["callback_url"]:
        channel.basic_publish(exchange="webhooks", routing_key="deliver",
                              body=json.dumps({"task_id": str(task_id), "status": "COMPLETED" if success else "FAILED", "result": result}),
                              properties=pika.BasicProperties(headers={"target_url": task["callback_url"]}, delivery_mode=2))
```

### Cancel (release reservation)

```python
async def cancel_task(jwt_claims, task_id):
    async with cmd_db.transaction():
        res = await cmd_db.fetchrow("""
            SELECT reservation_id, amount FROM credit_reservations
            WHERE task_id=$1 AND user_id=$2 AND state='RESERVED' FOR UPDATE
        """, task_id, jwt_claims.sub)
        if not res:
            raise HTTPException(409, "Not cancellable")
        await cmd_db.execute("UPDATE credit_reservations SET state='RELEASED', updated_at=now() WHERE reservation_id=$1", res["reservation_id"])
        await cmd_db.execute("UPDATE users SET credits=credits+$1 WHERE user_id=$2", res["amount"], jwt_claims.sub)
        await cmd_db.execute("UPDATE task_commands SET status='CANCELLED', updated_at=now() WHERE task_id=$1", task_id)
        await cmd_db.execute("INSERT INTO credit_transactions(user_id,task_id,delta,reason) VALUES($1,$2,$3,'cancel_release')",
                             jwt_claims.sub, task_id, res["amount"])
    await redis.hset(f"task:{task_id}", "status", "CANCELLED")
    return {"credits_refunded": res["amount"]}
```

### Watchdog (expired reservations)

```python
async def release_expired():
    expired = await cmd_db.fetch("""
        SELECT reservation_id, task_id, user_id, amount FROM credit_reservations
        WHERE state='RESERVED' AND expires_at < now()
        FOR UPDATE SKIP LOCKED
    """)
    for r in expired:
        async with cmd_db.transaction():
            await cmd_db.execute("UPDATE credit_reservations SET state='RELEASED' WHERE reservation_id=$1", r["reservation_id"])
            await cmd_db.execute("UPDATE users SET credits=credits+$1 WHERE user_id=$2", r["amount"], r["user_id"])
            await cmd_db.execute("UPDATE task_commands SET status='TIMEOUT' WHERE task_id=$1", r["task_id"])
            await cmd_db.execute("INSERT INTO credit_transactions(user_id,task_id,delta,reason) VALUES($1,$2,$3,'timeout_release')",
                                 r["user_id"], r["task_id"], r["amount"])
        await redis.hset(f"task:{r['task_id']}", "status", "TIMEOUT")
```

### Batch submission

```python
@app.post("/v1/task/batch")
async def submit_batch(jwt_claims, batch: BatchRequest):
    if len(batch.tasks) > 100:
        raise HTTPException(400, "Max 100 tasks")
    total_cost = sum(compute_cost(t.get("model_class", "small"), jwt_claims.tier) for t in batch.tasks)
    async with cmd_db.transaction():
        # Single reservation for batch
        batch_id = uuid7()
        # ... reserve total_cost, create individual task_commands, outbox events
    return {"batch_id": str(batch_id), "task_ids": [...], "total_cost": total_cost}
```

### Result expiry (24h TTL)

The watchdog also expires completed task results:

```python
async def expire_results():
    """Transition completed/failed tasks older than 24h to EXPIRED."""
    await db.execute("""
        UPDATE cmd.task_commands SET status='EXPIRED', updated_at=now()
        WHERE status IN ('COMPLETED', 'FAILED') AND updated_at < now() - interval '24 hours'
    """)
    await db.execute("""
        UPDATE query.task_query_view SET status='EXPIRED', updated_at=now()
        WHERE status IN ('COMPLETED', 'FAILED') AND updated_at < now() - interval '24 hours'
    """)
```

### Demo script

An example script (`demo.sh` / `demo.py`) is provided that authenticates, submits a task, polls until completion, and displays the result. Same pattern as solution 0, adapted for JWT auth flow.

## Dual-write status

SOLVED. The outbox pattern ensures command write + event publish are atomic:

1. Command + reservation + outbox row are in ONE Postgres transaction
2. Outbox relay reads unpublished rows and publishes to RabbitMQ
3. If relay crashes, it retries from unpublished rows on restart
4. Inbox dedup at consumer prevents double-processing

No refund/reaper jobs needed for billing. Reservation timeout is the safety net.

### Query timeout hardening

All queries are PK lookups or indexed single-row operations (expected <3ms). Timeouts are set at 10x expected latency at every layer:

- **PG `statement_timeout`**: 50ms (hot-path), 2s (watchdog batch). Server-side kill — frees PG resources, not just Python awaitables.
- **PG `idle_in_transaction_session_timeout`**: 500ms. Kills leaked transactions holding locks.
- **asyncpg `command_timeout`**: 100ms (2x server timeout). Client-side backup.
- **Redis `socket_timeout`**: 50ms on all production connections. Prevents event loop blocking on hung Redis.
- **Retry jitter**: All exponential backoff includes random jitter (`uniform(0.5, 1.5)`) to prevent thundering herds.

All values configurable via `.env.dev.defaults`.

## Reducing database calls

The core question: _"Assuming the database calls are too expensive, how can we reduce the number of calls?"_

This table shows DB calls per request type, comparing a naive PG-only approach vs solutions 0, 1, and this solution:

| Request                | Naive (PG only)  | Solution 0        | Solution 1           | **This solution**    | How this differs                                                          |
| ---------------------- | ---------------- | ----------------- | -------------------- | -------------------- | ------------------------------------------------------------------------- |
| **Auth (per request)** | 1 SELECT         | **0** (cache hit) | **0** (zero network) | **0** (zero network) | JWT local crypto verify; same as Sol 1                                    |
| **Submit**             | 4+ queries       | **1** PG txn      | **1** PG txn         | **1** PG txn         | Idempotency + reserve + command + outbox in one transaction               |
| **Poll (PENDING)**     | 1 SELECT         | **1** SELECT      | **0**                | **0**                | Redis write-through from submit populates cache                           |
| **Poll (COMPLETED)**   | 1 SELECT         | **0** (cache)     | **0** (cache)        | **0**                | Worker writes Redis on completion                                         |
| **Cancel**             | 2+ queries       | **1** PG txn      | **2** PG calls       | **1** PG txn         | Guarded release + command update + credit txn in one transaction          |
| **Admin credits**      | 2 queries        | **1** CTE         | **1** CTE            | **1** PG txn         | CTE + outbox in one transaction                                           |
| **Worker completion**  | 3+ queries       | 2 PG transitions  | 3 PG writes          | **1** PG txn         | Capture/release + command update + credit txn batched into one transaction |

**Net effect on a typical task lifecycle** (submit -> 5 polls -> complete):

- Naive: ~12 PG calls (1 auth + 4 submit + 5 polls + 2 worker)
- Solution 0: ~5 PG calls (1 submit + 2 PENDING polls + 2 worker transitions)
- Solution 1: ~4 PG calls (1 submit + 0 polls + 3 worker writes: RUNNING, terminal, checkpoint)
- **This solution: ~3 PG calls** (1 submit txn + 0 polls + 1 worker completion txn + 1 projection write)

The key difference vs solution 1: PG is on the COMMAND path (for transactional guarantees), but the call count is similar because operations are batched into transactions. The submit path packs idempotency check + credit reserve + command insert + outbox write into a single transaction. The worker completion path packs capture/release + command update + credit transaction into a single transaction. The result is comparable PG call counts with strictly stronger correctness guarantees.

## Degradation matrix

| Component down | Command routes                                       | Query routes                      | Workers | Webhooks         |
| -------------- | ---------------------------------------------------- | --------------------------------- | ------- | ---------------- |
| Redis          | Submit works (PG-only). Rate limit degraded.         | PG query schema fallback.         | Works   | Works            |
| Postgres       | 503                                                  | Redis cache works. Misses -> 503. | N/A     | N/A              |
| RabbitMQ       | Outbox holds events. Submit succeeds. Tasks delayed. | Works                             | Delayed | Queued in outbox |
| Workers        | Tasks queue. Reservation timeout releases credits.   | PENDING                           | N/A     | N/A              |

## Observability

### Compose containers: ~12

API, OAuth, worker, outbox-relay, projector, watchdog, webhook-worker, redis, postgres, rabbitmq, prometheus, grafana

### Logging and monitoring

- Same structlog + Prometheus + Grafana as solutions 0-1
- Additional metrics for reservations, outbox lag, queue depth, webhook delivery, projection lag, cache hit rate
- Prometheus alert rules (`monitoring/prometheus/alerts.yml` included). Alertmanager deployment is optional/planned.
- OTel SDK + Collector + Tempo for distributed tracing
- OpenSearch for searchable logs (planned; not included in current Sol 2 compose stack)

### Key metrics (on top of solution 1)

| Metric                                  | Type      |
| --------------------------------------- | --------- |
| `reservation_created_total{tier,mode}`  | counter   |
| `reservation_captured_total{tier,mode}` | counter   |
| `reservation_released_total{reason}`    | counter   |
| `reservation_age_seconds{state}`        | histogram |
| `outbox_unpublished_count`              | gauge     |
| `outbox_publish_lag_seconds`            | gauge     |
| `rabbitmq_queue_depth{queue}`           | gauge     |
| `rabbitmq_dlq_depth`                    | gauge     |
| `webhook_delivery_total{status}`        | counter   |
| `projection_lag_seconds`                | gauge     |
| `query_cache_hit_rate`                  | gauge     |

### Key alerts

| Alert                  | Condition                  | Severity |
| ---------------------- | -------------------------- | -------- |
| ReservationTimeoutRate | timeout releases > 5/min   | critical |
| OutboxBacklog          | unpublished > 50 for 2 min | critical |
| DLQNonEmpty            | depth > 0 for 10 min       | warning  |
| ProjectionLag          | lag > 30s for 5 min        | warning  |
| WebhookFailureRate     | retry rate > 20%           | warning  |

### Tracing

- `trace_id` propagated: API -> outbox -> RabbitMQ message header -> worker -> projector
- Full task lifecycle visible in Tempo via Grafana
- OTel Collector + Tempo available via `docker compose --profile tracing up -d`

## Test posture

- Unit tests: logic and contract behavior (CQRS routing, reservation state machine, outbox relay, SLA routing)
- Integration tests: running stack submit/poll/cancel/batch/admin/error/concurrency contracts
- E2E tests: demo script execution path
- Fault tests: Redis/RabbitMQ/PG/worker degradation and recovery behavior
- Scenario harness: multi-user concurrency, tier/mode stress, reservation timeout, webhook delivery, cancel-while-processing

## Known limitations

- **Consumer lag risk from RabbitMQ**: RabbitMQ is designed for flow-through, not storage. When the projector or webhook consumer is down for extended periods, messages accumulate and memory pressure builds. Unlike Redpanda/Kafka, consumed messages are gone -- no replay. The query view can be rebuilt from the command table via SQL join, but RabbitMQ itself provides no reprocessing safety net.
- **Projection delay**: The query view (`query.task_query_view`) is updated asynchronously via the projector consuming from RabbitMQ. During projector lag, the query view is stale. Clients see correct results via Redis cache (which is updated synchronously on the response plane), but any system relying on the query view (admin dashboards, reporting) will be delayed.
- **Reservation timeout precision**: Reservation expiry is handled by the watchdog polling on an interval (e.g., every 30s). This means a reservation could live up to `timeout + poll_interval` before being released. For the 10-minute default timeout, this is acceptable. For sub-minute precision, a different mechanism (e.g., PG `LISTEN/NOTIFY` or scheduler) would be needed.
- **No event replay**: RabbitMQ consumed = gone. If a projector bug corrupts the query view, recovery requires rebuilding from the command table via SQL -- not replaying events. This is adequate for this solution but is a structural limit that solution 3 (Redpanda) addresses.
- **App-coordinated reservation correctness**: Reservation state transitions are enforced by application SQL (`WHERE state='RESERVED' FOR UPDATE`), not by a dedicated financial engine. This is correct under normal operation and row-level locking, but lacks the double-entry invariants that TigerBeetle would provide.

## Degree of constraint

- Optimized for: correctness under all failure modes, independent read/write scaling
- Pattern: CQRS + outbox (reliable publish). Not event sourcing -- command table is the source of truth.
- Current ceiling: no event replay (RabbitMQ consumed = gone), reservation correctness is app-coordinated SQL
- Consumer lag risk: RabbitMQ degrades when projector/webhook consumers are down for extended periods. Messages accumulate, memory pressure builds. RabbitMQ is designed for flow-through, not storage.
- Query view rebuildable from command table via SQL join (no event log needed)
- Migration triggers: need financial-grade billing -> solution 3 (TigerBeetle), need safe consumer lag -> solution 3 (Redpanda), need model-affinity dispatch -> solution 3 (hot/cold routing)

## Alternatives considered

- Keep Redis Streams from solution 1: fewer services, but no SLA routing or DLQ ergonomics
- Move directly to Redpanda: replay support, but one major axis of change per step
- TigerBeetle for reservations: stronger guarantees, but next-step specialization

### Considered: Durable execution (Restate, Temporal)

Durable execution engines journal each step of a multi-service workflow, providing automatic retry and compensation without an explicit outbox table or relay service.

[Restate](https://restate.dev) is particularly relevant: single Rust binary, Python SDK, sub-10ms per-step overhead, fits in Docker Compose. It would replace the outbox table + outbox-relay + watchdog compensation code with journaled handler functions where each `ctx.run()` call is durably recorded and retried on failure.

[Temporal](https://temporal.io) provides the same guarantees but requires a cluster (server + Cassandra/Postgres backing store), making it heavier for Docker Compose deployments.

We chose the outbox pattern because:

1. **Pattern, not product** -- the outbox is a design pattern with no runtime dependency. It works with any message broker (RabbitMQ, Redpanda, Kafka) and survives vendor changes.
2. **Composes with CQRS** -- the outbox fits naturally into the command-side transaction. Events flow to projectors and webhook consumers through the same relay.
3. **Team familiarity** -- the outbox is a Postgres transaction + a SQL poller. No new execution model to learn.
4. **Incremental migration** -- if operational burden of the relay warrants it, Restate handlers can consume the same outbox events, making adoption incremental rather than rewrite.

Restate is a strong candidate for post-launch evaluation once the outbox relay's operational cost is measurable. The crossover point is when relay monitoring, retry tuning, and dead-letter handling consume more engineering time than adopting a durable execution runtime.

## Capacity model

Target: 50K customers, 30M submits/day, 150M polls/day.

Assumptions: `W=6, C=4, u=0.70, T=2.5s, P=1.5`

| Resource        | Steady-state  | Key driver                 |
| --------------- | ------------- | -------------------------- |
| Task throughput | 6.72 task/sec | `R_task = (6*4*0.70)/2.5`  |
| Poll throughput | 10.08 req/sec | `R_poll = R_task * P`      |
| Monthly tasks   | 17,418,240    | `R_task * 86400 * 30`      |
| Monthly polls   | 26,127,360    | `R_poll * 86400 * 30`      |

For the full hourly traffic model, worker/API/queue scaling curves, storage math per table, and infrastructure summary, see [Capacity Model](./capacity-model.md).

## References

- [Request Flow Diagrams](./request-flows.md)
- [Data Ownership and Consistency](./data-ownership.md)
- [Capacity and Cost Model](./capacity-model.md)
- `../../README.md`
- `../../0_0_problem_statement_and_assumptions/README.md`
