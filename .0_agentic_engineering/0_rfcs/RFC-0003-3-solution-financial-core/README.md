# RFC-0003: Financial Core - TigerBeetle + Redpanda + CQRS Projections

- **Status:** Implemented
- **Owner:** Venkat
- **Date:** 2026-02-15
- **Solution slot:** `3_solution`
- **Deployment constraint:** Docker Compose only

## Context and scope

Solution 2 solves dual-write and reservation billing, but two structural gaps remain: (1) reservation correctness is app-coordinated SQL - one missed `WHERE state='RESERVED'` clause and credits leak, (2) RabbitMQ consumed = gone - there is no event replay, so projections cannot be rebuilt and audit trails are reconstructed from DB snapshots, not from a replayable source of truth.

This solution replaces app-coordinated billing with TigerBeetle (Jepsen-verified double-entry accounting) and adds Redpanda (Kafka API compatible, replayable log) as the event backbone. RabbitMQ is retained but with a narrowed role: worker dispatch with hot/cold model-affinity routing. The result: billing invariants are enforced by TigerBeetle's state machine (not application code), every projection is rebuildable by replaying the event log from offset 0, and workers receive tasks routed to warm instances first.


What changed vs solution 2:

- TigerBeetle handles pending/post/void transfer lifecycle (replaces credit_reservations table + watchdog)
- Redpanda provides replayable event backbone (event log, projections, analytics feed)
- RabbitMQ role narrowed to worker dispatch only (hot/cold model-affinity routing via header exchanges)
- CQRS projections are rebuildable from event log (impossible in solutions 0-2)
- Reconciler replaces watchdog (checks TB transfer state for stale pendings)
- Redis remains as query cache layer
- Optional future extension: ClickHouse for business event OLAP analytics

### Infrastructure justification

Every infrastructure component earns its place by doing something the others cannot:

| Infra       | Role                                     | Why not another?                                                                                                                                                                                                            |
| ----------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TigerBeetle | Double-entry credit transfers            | PG transactions work but aren't Jepsen-verified. TB pending/post/void is purpose-built.                                                                                                                                     |
| Redpanda    | Durable event log with safe consumer lag | RabbitMQ degrades under consumer lag (projector down = message accumulation = memory pressure). Redpanda is designed for consumers to lag, crash, and catch up. Also enables independent consumer reset and analytics feed. |
| RabbitMQ    | Worker dispatch routing                  | Redpanda can't do broker-side "try warm first, fall back to cold." Log consumption is round-robin.                                                                                                                          |
| PostgreSQL  | Metadata + CQRS projections              | TB stores balances, not task metadata. Redpanda stores events, not queryable state.                                                                                                                                         |
| Redis       | Hot-path cache + active counters         | PG for every auth/balance lookup = the "too many DB calls" problem the design problem.                                                                                                                         |

What this solution does NOT include:

- Multi-region active-active
- Cross-currency or multi-asset accounting
- Saga orchestrator (TigerBeetle + outbox is sufficient for this domain)

Implementation boundary for the coded `3_solution` track:

- Shipped code includes Prometheus, Grafana, and alert rules
- OTel/Tempo, OpenSearch, and ClickHouse stay RFC-only optional extensions
- Those extensions are intentionally out of scope for calling Solution 3 complete
- Batch endpoint and batch-processing mode are RFC-only (not implemented in shipped code)

### What this is and is not

This is **CQRS + event-driven projections with a replayable log**, not event sourcing.

- **CQRS**: Same as solution 2 — command and query paths separated in code (routers, schemas).
- **Event-driven projections**: The query view is updated by a projector consuming Redpanda events. The query side IS rebuildable from the event log (reset offset to 0, truncate, replay).
- **Replayable log**: Redpanda retains events. Independent consumers (projector, webhook, analytics) can reset offsets without affecting each other.
- **NOT event sourcing**: The command table (`cmd.task_commands`) + TigerBeetle are the source of truth, mutated directly. Events are published AFTER state changes, not the other way around. Command state cannot be rebuilt from events — nor does it need to be.
- **Query side rebuild from PG**: The query view can also be rebuilt with `INSERT INTO query.task_query_view SELECT ... FROM cmd.task_commands` — Redpanda is not the only path to rebuild.

The replayable log adds value for: independent consumer lifecycle (add/reset without schema changes), temporal decoupling (consumers catch up from lag), and clean separation of history from current state. It does NOT provide event sourcing guarantees.

## Goals and non-goals

### Goals

- Billing correctness enforced by infrastructure, not application code
- Query-side projection rebuild via replayable event log
- Independent consumer lifecycle (projector, webhook, analytics can reset/add without coordination)
- Hot/cold model-affinity worker dispatch
- Same reserve/capture/release semantics but with TigerBeetle guarantees

### Non-goals

- Event sourcing (command state is direct, not derived from events)
- Sub-millisecond billing latency (TB is fast, but network hop exists)
- Complex financial products (multi-leg, cross-currency, interest accrual)

## The actual design

### Design summary

- API: Single FastAPI process with command routes (write path) and query routes (read path) + OAuth service
- Billing: TigerBeetle pending/post/void transfers (double-entry, Jepsen-verified)
- Event backbone: Redpanda (Kafka API, single binary, replayable log)
- Worker dispatch: RabbitMQ with hot/cold model-affinity routing (header exchanges, preloaded/coldstart)
- Dispatcher: Redpanda consumer -> RabbitMQ publisher (bridges event log to work dispatch)
- DB: Single Postgres instance, `cmd` schema (tasks, outbox) + `query` schema (task_query_view)
- Projections: Redpanda consumer rebuilds query model in Redis + Postgres query schema
- Analytics: ClickHouse for business event OLAP (optional Compose profile, Grafana datasource)
- Reconciler: detects stale pending transfers and aligns command DB with TB state

Redpanda and RabbitMQ serve non-overlapping roles:

- **Redpanda = what happened** (event log: retained, replayable, feeds projections + analytics)
- **RabbitMQ = who should do it** (dispatch: broker-side routing, consumed and acknowledged, transient)

### Communication planes

```text
COMMAND PLANE (durable, multi-hop, correctness over latency)
  API -> TB pending transfer -> PG outbox (txn) -> Relay -> Redpanda -> Dispatcher -> RabbitMQ -> Worker
  Each hop: billing, durability, event log, bridge, model-affinity routing

RESPONSE PLANE (direct, 1 hop, latency-optimized)
  Worker -> TB post/void + PG update + Redis write-through -> Client polls Redis
  No queues in the response path. Result visible in Redis immediately after
  worker completion. Client never waits for projector or Redpanda.

EVENT PLANE (background, fan-out, consumers can lag safely)
  Redpanda -> Projector (PG query view + Redis)
           -> Dispatcher (-> RabbitMQ worker routing)
           -> Webhook worker (HTTP callbacks)
           -> Event exporter (ClickHouse, optional)
  Consumer-independent. Lag is normal. Any consumer can crash, restart,
  and catch up from last committed offset. This is why Redpanda, not
  RabbitMQ, carries the event path -- RabbitMQ degrades under consumer lag,
  Redpanda is designed for it.
```

The command and response planes are independent. The command plane optimizes for correctness (every hop prevents a failure mode). The response plane optimizes for latency (direct Redis write-through, no queues). The event plane runs in the background — consumers catch up at their own pace without affecting the client-facing response.

Note: Same as solution 2 - CQRS separation is in code (separate routers, schemas), not containers.

### TigerBeetle account model

```text
Account type               | ID scheme       | flags
---------------------------|-----------------|---------------------------------
User Credit Account        | user_id mapped  | debits_must_not_exceed_credits
Platform Revenue           | fixed: 1000001  | (none)
Escrow (holds pending)     | fixed: 1000002  | (none)

Available credits = credits_posted - debits_posted - debits_pending
```

Every user gets a TB account on first auth. Seed data users get accounts created at startup.

Transfer lifecycle:

```text
  pending_transfer(user -> escrow, amount=cost, timeout=600s)
    |
    +--> post_pending_transfer   (escrow -> revenue)   = CAPTURED
    +--> void_pending_transfer   (credits auto-return) = RELEASED
    +--> timeout (auto-void)     (credits auto-return) = EXPIRED
```

This is a two-phase transfer. The pending transfer debits the user and credits escrow. Post moves escrow to revenue. Void reverses the debit. TigerBeetle enforces `debits_must_not_exceed_credits` atomically - no application code can overdraft.

### System-context diagram

```text
Client
  |
  +-- POST /v1/oauth/token -------> OAuth Service
  |
  +-- POST /v1/task (JWT) ---------- command routes
  +-- POST /v1/task/{id}/cancel --- command routes
  +-- POST /v1/admin/credits ------ command routes
  +-- GET  /v1/poll ---------------- query routes
  |
  v
+---------------------------+
| API (FastAPI)             |
| cmd routes  | query routes|
+------+------+------+------+
       |             |
  +----+----+--------+--------+
  |         |                 |
  v         v                 v
+--------+ +-----------+  +--------+
|Postgres| |TigerBeetle|  | Redis  |
|cmd.*   | |pending    |  | query  |
|query.* | |post/void  |  | cache  |
|        | |accounts   |  | active |
+---+----+ +-----------+  +--------+
    |
    | outbox relay
    +---------->  +-----------+
                  | Redpanda  |  (event log: retained, replayable)
                  | tasks.*   |
                  +-----+-----+
                        |
         +--------------+-------------+-----------+
         |              |             |           |
         v              v             v           v
  +------------+ +----------+ +-----------+ +-----------+
  | Dispatcher | |Projector | | Webhook   | | Event     |
  | Redpanda-> | |query     | | Worker    | | Exporter  |
  | RabbitMQ   | |schema    | | callbacks | | ->ClickHse |
  +-----+------+ +----------+ +-----------+ +-----------+
        |                                     (optional)
        v
  +-------------------+
  | RabbitMQ          |  (worker dispatch: transient, routed)
  |                   |
  | preloaded exch    |---> hot-small queue ----> Worker (warm: small)
  | (header-based)    |---> hot-medium queue ---> Worker (warm: medium)
  |       |           |---> hot-large queue ----> Worker (warm: large)
  |       | (returned)|
  |       v           |
  | coldstart exch    |---> cold queue ---------> Worker (loads any model)
  +-------------------+

+------------+
| Reconciler |  checks TB pending state for stale task_commands
+------------+

+-----------+  +---------+
| Prometheus|  | Grafana |
+-----------+  +---------+
```

## APIs

### Public endpoints

`POST /v1/oauth/token` - same as solutions 1-2

`POST /v1/task` (JWT)

- Body: `{"x": 5, "y": 3, "model_class": "medium", "mode": "async", "callback_url": "https://..."}`
- Success: `201 {"task_id": "...", "status": "PENDING", "billing_state": "RESERVED", "queue": "fast", "expires_at": "..."}`
- Errors: 401, 402, 409 (idempotency), 429 (concurrency)

`POST /v1/task/batch` (RFC-only, not implemented in shipped API)

- RFC design includes batch submission; current shipped implementation does not route this endpoint.
- A request to this route currently returns `404` in the implemented scope.

`GET /v1/poll?task_id=<uuid>` (JWT)

- Reads from query API (Redis cache -> Postgres query view fallback)
- Success: `200 {"task_id": "...", "status": "...", "billing_state": "...", "result": ..., "expires_at": "..."}`
- Queue position and estimated time are not provided (CQRS projections are eventually consistent). Clients use `expires_at` for timeout decisions.
- Results transition to EXPIRED after 24h TTL via the reconciler.

`POST /v1/task/{id}/cancel` (JWT)

- Voids pending transfer. Returns refunded credits.

`POST /v1/admin/credits` (admin JWT)

- Direct transfer (not pending) from platform revenue to user account.

### Internal contracts

- Redpanda topics: `tasks.requested`, `tasks.started`, `tasks.completed`, `tasks.failed`, `tasks.cancelled`, `tasks.expired`
- Outbox event envelope: `event_id, task_id, user_id, tier, mode, model_class, cost, tb_transfer_id, topic, schema_version`
- Inbox dedup key: `event_id`
- RabbitMQ exchanges:
  - `preloaded` (headers exchange, x-match=all): routes to `hot-{model_class}` queues when a warm worker binding exists
  - `coldstart` (headers exchange, x-match=all): fallback when `preloaded` returns the message (no matching binding)
  - Alternate exchange: `preloaded` -> `coldstart` (RabbitMQ `alternate-exchange` argument)
- RabbitMQ queues: `hot-small`, `hot-medium`, `hot-large`, `cold`
- RabbitMQ headers: `model_class`, `tier`, `task_id`

## Code and pseudo-code

This section shows the key submit and worker completion paths. For full request-flow diagrams covering all endpoints, see [Request Flows](./request-flows.md).

### Submit path (TigerBeetle + outbox)

```python
async def submit_task(jwt_claims, payload, idem_key):
    tier = jwt_claims.tier
    model = payload.get("model_class", "small")
    cost = compute_cost(model, tier)
    task_id = uuid7()
    transfer_id = uuid7()

    # 1. TigerBeetle: create pending transfer (user -> escrow)
    result = tb_client.create_transfers([
        Transfer(
            id=uuid_to_u128(transfer_id),
            debit_account_id=uuid_to_u128(jwt_claims.sub),
            credit_account_id=ESCROW_ID,
            amount=cost, ledger=1, code=200,
            flags=TransferFlags.PENDING,
            timeout=600,  # seconds - auto-void after 10 min
        )
    ])
    if result[0].result == CreateTransferResult.EXCEEDS_CREDITS:
        raise HTTPException(402, "INSUFFICIENT_CREDITS")

    # 2. Check concurrency via Redis counter
    active_count = int(await redis.get(f"active:{jwt_claims.sub}") or "0")
    if active_count >= get_max_concurrent(tier):
        # Void the transfer we just created (compensate)
        tb_client.create_transfers([Transfer(
            id=new_transfer_id(), pending_id=uuid_to_u128(transfer_id),
            flags=TransferFlags.VOID_PENDING_TRANSFER,
        )])
        raise HTTPException(429, "CONCURRENCY_LIMIT")
    await redis.incr(f"active:{jwt_claims.sub}")

    # 3. Postgres: command row + outbox (one transaction)
    async with cmd_db.transaction():
        # Idempotency check
        existing = await cmd_db.fetchval(
            "SELECT task_id FROM task_commands WHERE user_id=$1 AND idempotency_key=$2",
            jwt_claims.sub, idem_key)
        if existing:
            tb_client.create_transfers([Transfer(
                id=new_transfer_id(), pending_id=uuid_to_u128(transfer_id),
                flags=TransferFlags.VOID_PENDING_TRANSFER)])
            return {"task_id": str(existing)}

        await cmd_db.execute("""
            INSERT INTO task_commands(task_id, user_id, tier, mode, model_class,
                x, y, cost, tb_pending_transfer_id, billing_state, callback_url, idempotency_key)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'RESERVED',$10,$11)
        """, task_id, jwt_claims.sub, tier, payload.get("mode","async"), model,
             payload["x"], payload["y"], cost, transfer_id,
             payload.get("callback_url"), idem_key)

        await cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, 'task.requested', 'tasks.requested', $2)
        """, task_id, json.dumps({
            "task_id": str(task_id), "user_id": str(jwt_claims.sub),
            "tier": tier, "model_class": model, "cost": cost,
            "tb_transfer_id": str(transfer_id),
            "x": payload["x"], "y": payload["y"],
        }))

    # 4. Write-through to Redis query cache
    await redis.hset(f"task:{task_id}", mapping={
        "status": "PENDING", "billing_state": "RESERVED",
    })
    return {"task_id": str(task_id), "billing_state": "RESERVED"}
```

### Worker completion (post/void pending transfer)

```python
def on_task_complete(task_id, tb_transfer_id, user_id, cost, success, result=None, error=None):
    if success:
        # Post pending transfer: escrow -> platform revenue (credits captured)
        tb_client.create_transfers([Transfer(
            id=new_transfer_id(), pending_id=uuid_to_u128(tb_transfer_id),
            flags=TransferFlags.POST_PENDING_TRANSFER,
        )])
        billing_state, status, topic = "CAPTURED", "COMPLETED", "tasks.completed"
    else:
        # Void pending transfer: credits auto-return to user
        tb_client.create_transfers([Transfer(
            id=new_transfer_id(), pending_id=uuid_to_u128(tb_transfer_id),
            flags=TransferFlags.VOID_PENDING_TRANSFER,
        )])
        billing_state, status, topic = "RELEASED", "FAILED", "tasks.failed"

    # Update command DB + outbox (one transaction)
    with cmd_db.transaction():
        cmd_db.execute("""
            UPDATE task_commands SET status=$1, billing_state=$2, updated_at=now()
            WHERE task_id=$3
        """, status, billing_state, task_id)
        cmd_db.execute("""
            INSERT INTO outbox_events(aggregate_id, event_type, topic, payload)
            VALUES($1, $2, $3, $4)
        """, task_id, topic, topic, json.dumps({
            "task_id": str(task_id), "user_id": str(user_id),
            "status": status, "billing_state": billing_state,
            "result": result, "error": error, "cost": cost,
        }))

    # Decrement active counter + write-through to query cache
    redis.decr(f"active:{user_id}")
    redis.hset(f"task:{task_id}", mapping={
        "status": status, "billing_state": billing_state,
        "result": json.dumps(result) if result else "",
    })
```

For additional flows (outbox relay, dispatcher, projector, reconciler, webhook delivery, projection rebuild), see [Request Flows](./request-flows.md).

## Dual-write status

SOLVED (same pattern as solution 2, stronger billing).

1. TigerBeetle pending transfer is created BEFORE Postgres transaction
2. If Postgres transaction fails: void the pending transfer (compensate)
3. If API crashes between TB and PG: reconciler detects stale pending, TB auto-voids after timeout
4. Outbox relay publishes to Redpanda (atomic with command write)
5. Inbox dedup prevents double-processing at consumers

Credit invariant is enforced by TigerBeetle, not application SQL. The application cannot overdraft even if there is a bug in the reservation logic.

## Degradation matrix

| Component down | Command routes                                                     | Query routes                      | Workers                                                                                        | Analytics |
| -------------- | ------------------------------------------------------------------ | --------------------------------- | ---------------------------------------------------------------------------------------------- | --------- |
| TigerBeetle    | 503 for submit (can't reserve). Query works.                       | Works from projection             | Work pauses (can't capture/release)                                                            | Works     |
| Redpanda       | Outbox holds events. Submit succeeds. Projection delayed.          | Redis cache works. Stale data.    | Dispatcher stalls. No new dispatch.                                                            | Delayed   |
| RabbitMQ       | Submit succeeds. Outbox + Redpanda hold events.                    | Works                             | Dispatch stalls. Tasks queue in Redpanda until RabbitMQ recovers. TB timeout protects credits. | Works     |
| Postgres       | 503                                                                | Redis cache works. Misses -> 503. | N/A                                                                                            | N/A       |
| Redis          | Submit works (PG + TB only).                                       | PG query schema fallback.         | Works (warm registry stale)                                                                    | Works     |
| Workers        | Tasks queue in RabbitMQ + Redpanda. TB pending timeout auto-voids. | PENDING (stale)                   | N/A                                                                                            | Works     |
| ClickHouse     | Full system works. Analytics unavailable.                          | Works                             | Works                                                                                          | 503       |

Key differences from solution 2: (1) when workers are down, TigerBeetle's pending transfer timeout (600s) auto-voids the debit - no watchdog needed, (2) when RabbitMQ is down, Redpanda retains all events - the dispatcher replays from last committed offset when RabbitMQ recovers, no message loss.

## Observability

### Implemented

- Same structlog + Prometheus + Grafana as solutions 0-2
- Additional metrics for Redpanda consumer lag, projection rebuild, reconciler, and worker throughput

### RFC-only optional extensions (not required for coded Solution 3 completeness)

- Alertmanager alerts are defined in Prometheus rules; running Alertmanager is optional and RFC-only
- OTel SDK + Collector + Tempo for distributed tracing
- OpenSearch for searchable logs
- ClickHouse for business event OLAP (optional Compose profile: `docker compose --profile analytics up`)

### Compose containers: ~15

API, OAuth, dispatcher, worker(s), outbox-relay, projector, reconciler, webhook-worker, redis, postgres, tigerbeetle, redpanda, rabbitmq, prometheus, grafana
plus one-shots: migrate, hydra-migrate, hydra-client-init, tb-init

With `--profile tracing`: +tempo (~16 total)
With `--profile analytics`: +event-exporter, +clickhouse (~17 total)

### New metrics (on top of solution 2)

| Metric                                       | Type                      |
| -------------------------------------------- | ------------------------- |
| `redpanda_consumer_lag{group,topic}`         | gauge                     |
| `redpanda_produce_latency_seconds{topic}`    | histogram                 |
| `projection_events_processed_total{topic}`   | counter                   |
| `projection_rebuild_duration_seconds`        | histogram                 |
| `projection_lag_events`                      | gauge                     |
| `reconciler_stale_found_total`               | counter                   |
| `reconciler_resolved_total{resolution,status}` | counter                  |
| `clickhouse_insert_batch_size`               | histogram                 |
| `clickhouse_insert_latency_seconds`          | histogram                 |
| `dispatch_published_total{exchange}`         | counter                   |
| `dispatch_hot_hit_total{model_class}`        | counter                   |
| `dispatch_cold_fallback_total{model_class}`  | counter                   |
| `worker_model_load_seconds{model_class}`     | histogram                 |
| `worker_warm_inference_seconds{model_class}` | histogram                 |

### Key alerts

| Alert                   | Condition                                    | Severity |
| ----------------------- | -------------------------------------------- | -------- |
| TigerBeetleDown         | TB health check fails for 30s                | critical |
| TBTransferFailRate      | non-EXCEEDS_CREDITS failures > 1%            | critical |
| RedpandaConsumerLag     | lag > 10000 for 5 min                        | critical |
| ProjectionLag           | lag > 60s for 5 min                          | warning  |
| ReconcilerStalePendings | stale count > 10                             | warning  |
| OutboxBacklog           | unpublished > 50 for 2 min                   | critical |
| ClickHouseInsertLag     | lag > 5 min                                  | warning  |
| RevenueAnomaly          | captured/released ratio deviates > 2 std dev | warning  |
| HighColdStartRate       | cold fallback > 50% of dispatches for 5 min  | warning  |
| RabbitMQDown            | RabbitMQ health check fails for 30s          | critical |
| DispatcherLag           | Redpanda consumer lag for dispatcher > 100   | warning  |

### ClickHouse schema

```sql
CREATE TABLE events (
  event_id UUID,
  event_type LowCardinality(String),
  task_id UUID,
  user_id UUID,
  tier LowCardinality(String),
  mode LowCardinality(String),
  model_class LowCardinality(String),
  cost UInt32,
  status LowCardinality(String),
  billing_state LowCardinality(String),
  ts DateTime64(3)
) ENGINE = MergeTree()
ORDER BY (event_type, ts)
PARTITION BY toYYYYMM(ts)
TTL ts + INTERVAL 365 DAY;
```

### ClickHouse OLAP queries (via Grafana)

```sql
-- Revenue by tier, last 24h
SELECT tier, sum(cost) as revenue, count() as tasks
FROM events WHERE event_type = 'tasks.completed' AND ts > now() - interval 1 day
GROUP BY tier;

-- Task completion rate by model class
SELECT model_class,
       countIf(event_type = 'tasks.completed') / count() as success_rate
FROM events WHERE ts > now() - interval 1 hour
GROUP BY model_class;

-- User activity heatmap
SELECT toStartOfHour(ts) as hour, user_id, count() as tasks
FROM events WHERE event_type = 'tasks.requested' AND ts > now() - interval 7 day
GROUP BY hour, user_id;
```

## Degree of constraint

- Optimized for: billing correctness enforced by infrastructure, event-driven projections, model-affinity dispatch
- Current ceiling: TigerBeetle is single-node in Compose (multi-node requires cluster config), ClickHouse retention vs storage cost
- This is the terminal solution: no further evolution needed in this series
- Explicit non-goal: event sourcing. Command state is direct (PG + TB), not derived from events.

## Alternatives considered

- **Redpanda only (drop RabbitMQ)**: Kafka-style log consumption is round-robin within a consumer group - no broker-side routing. Hot/cold model affinity requires broker-side "try warm first, fall back to cold." RabbitMQ header exchanges with alternate-exchange fallback do this natively. Redpanda cannot.
- **RabbitMQ only (drop Redpanda)**: solves dispatch, loses replayable log. Query view can still be rebuilt from command table (SQL), analytics can be ETL'd from PG. Redpanda adds: independent consumer lifecycle (add/reset without schema changes), temporal decoupling for consumer lag, and clean history separation. Whether that justifies an extra infrastructure component is a legitimate tradeoff — at 17 tasks/sec, PG handles everything Redpanda does.
- **Consumer-side filtering in Redpanda**: every worker reads every message and filters by header. Wastes I/O, and Kafka consumer groups do not support "skip and re-route" - offset advances regardless.
- **Postgres as event store**: works, but replay is slower than Redpanda and no native consumer groups.
- **Skip ClickHouse**: viable (Grafana Prometheus handles most queries), included because it shows OLAP thinking without additional application complexity.
- **Kafka instead of Redpanda**: heavier (JVM + ZooKeeper), Redpanda is single binary and Kafka API compatible.

## Capacity summary

Assumptions: `W=8, C=6, u=0.70, T=2.0s, P=1.2`

- `R_task = (8*6*0.70)/2.0 = 16.80 task/sec`
- `R_poll = 20.16 req/sec`
- `M_task = 43,545,600/month`
- `M_poll = 52,254,720/month`

TigerBeetle handles ~1M transfers/sec on commodity hardware and will never be the bottleneck. Redpanda single-node sustains ~100K msg/sec, also not a bottleneck at this scale.

For the full capacity model with storage math, scaling triggers, and cost projections, see [Capacity Model](./capacity-model.md).

## References

- [Request Flow Diagrams](./request-flows.md) — full per-endpoint flows (submit, cancel, batch, dispatcher, projector, reconciler, webhook, projection rebuild)
- [Data Ownership and Consistency](./data-ownership.md) — store responsibilities, schema details, retention, index strategy, consistency boundaries
- [Capacity and Cost Model](./capacity-model.md) — throughput formula, storage projections, scaling triggers
- [Sol 1 RFC](../RFC-0001-1-solution-redis-native-engine/README.md)
- [Sol 2 RFC](../RFC-0002-2-solution-service-grade-platform/README.md)
- `../../README.md`
