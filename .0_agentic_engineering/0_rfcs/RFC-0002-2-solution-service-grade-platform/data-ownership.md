# RFC-0002: Data Ownership and Consistency

Parent: [RFC-0002 README](./README.md)

## Data ownership model

Solution 2 uses three stores with clear ownership boundaries. Postgres is the single source of truth for all mutable state. Redis is a query cache. RabbitMQ is a message transport.

| Data                     | Redis (query cache)                   | Postgres `cmd` schema                                       | Postgres `query` schema       | RabbitMQ                            | Source of truth                                      | On component restart                                          |
| ------------------------ | ------------------------------------- | ----------------------------------------------------------- | ----------------------------- | ----------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------- |
| **User identity**        | -                                     | `users`, `api_keys`                                         | -                             | -                                   | Postgres `cmd`                                       | N/A; always in Postgres                                       |
| **Credit balance**       | -                                     | `users.credits` (available = balance after reserve deducts) | -                             | -                                   | Postgres `cmd`                                       | N/A; always in Postgres                                       |
| **Credit reservations**  | -                                     | `credit_reservations` (RESERVED/CAPTURED/RELEASED)          | -                             | -                                   | Postgres `cmd`                                       | N/A; watchdog releases expired on recovery                    |
| **Credit audit**         | -                                     | `credit_transactions` (immutable log)                       | -                             | -                                   | Postgres `cmd`                                       | N/A; always in Postgres                                       |
| **Task state (command)** | -                                     | `task_commands` (mutable, UPDATE in place)                  | -                             | -                                   | Postgres `cmd`                                       | N/A; always in Postgres                                       |
| **Task state (query)**   | `task:{id}` hash (TTL 24h)            | -                                                           | `task_query_view` (projected) | -                                   | Postgres `cmd` via projection; Redis is optimization | Cache miss -> query view -> cmd join fallback                 |
| **Idempotency**          | -                                     | unique constraint on `(user_id, idempotency_key)`           | -                             | -                                   | Postgres `cmd`                                       | N/A; constraint survives restart                              |
| **Task queue**           | -                                     | -                                                           | -                             | tiered queues (realtime/fast/batch) | RabbitMQ (durable queues)                            | Durable queues survive broker restart; unacked redelivered    |
| **Outbox events**        | -                                     | `outbox_events` (published_at NULL = pending)               | -                             | published copy (consumed = gone)    | Postgres `cmd`                                       | Relay retries unpublished rows on restart                     |
| **Inbox dedup**          | -                                     | `inbox_events` (event_id + consumer_name)                   | -                             | -                                   | Postgres `cmd`                                       | N/A; always in Postgres                                       |
| **Token revocation**     | `revoked:{uid}:{day}` set (hot cache) | `token_revocations` (day-partitioned)                       | -                             | -                                   | Postgres durable; Redis hot cache                    | Rehydrate Redis from PG on startup; PG fallback on cache miss |
| **Rate limit counters**  | `ratelimit:*` counters (TTL-bounded)  | -                                                           | -                             | -                                   | Redis only                                           | Reset to 0; converges within window as new requests arrive    |
| **DLQ messages**         | -                                     | -                                                           | -                             | DLQ (durable, 7-day retention)      | RabbitMQ                                             | Durable; survives broker restart                              |

Key difference from Sol 0/Sol 1: **Postgres is the source of truth for everything that matters.** Redis is never the billing authority. RabbitMQ is a transport, not a store.

## Consistency boundaries

### Atomic boundary (single Postgres transaction)

The submit path writes command + reservation + outbox + credit deduction in ONE Postgres transaction:

```text
BEGIN
  1. Idempotency check     (SELECT task_id WHERE user_id + idempotency_key)
  2. Concurrency check     (SELECT COUNT(*) FROM credit_reservations WHERE state='RESERVED')
  3. Credit deduction       (UPDATE users SET credits=credits-cost WHERE credits>=cost)
  4. Reservation insert    (INSERT INTO credit_reservations state='RESERVED')
  5. Task command insert   (INSERT INTO task_commands status='PENDING')
  6. Outbox event insert   (INSERT INTO outbox_events published_at=NULL)
COMMIT
```

All six operations succeed or none do. No partial state. No compensation needed for submit failures. This is the core improvement over Sol 0/Sol 1, where credit deduction and task creation were in separate stores.

Worker completion is also atomic within Postgres:

```text
BEGIN
  1. Lock reservation       (SELECT ... FOR UPDATE WHERE state='RESERVED')
  2. Capture or release     (UPDATE credit_reservations SET state='CAPTURED'|'RELEASED')
  3. Credit refund (if release)  (UPDATE users SET credits=credits+amount)
  4. Audit log              (INSERT INTO credit_transactions)
  5. Task status update     (UPDATE task_commands SET status='COMPLETED'|'FAILED')
COMMIT
```

### Cross-store boundary (outbox-guaranteed): PG -> RabbitMQ

The outbox pattern provides at-least-once delivery from Postgres to RabbitMQ:

```text
PG outbox_events (published_at IS NULL)
    |
    v
Outbox relay reads batch (LIMIT 100, ORDER BY created_at)
    |
    v
basic_publish(exchange="tasks", routing_key=..., delivery_mode=2)
    |
    v
UPDATE outbox_events SET published_at=now() WHERE event_id=...
```

If the relay crashes between publish and marking published, the row remains unpublished. On restart, the relay re-reads it and publishes again. RabbitMQ gets a duplicate. The inbox dedup table at the consumer prevents double-processing:

```text
Consumer receives message with event_id
    |
    v
INSERT INTO inbox_events(event_id, consumer_name) -- fails on duplicate PK
    |
    v
If insert succeeds: process message. If duplicate: ACK and skip.
```

**Guarantee:** every outbox event is published to RabbitMQ at least once and processed by each consumer at most once.

### Cross-store boundary (write-through cache): PG -> Redis

After Postgres commit, the API and worker write task status to Redis as a cache optimization:

```text
PG COMMIT (task status = PENDING/COMPLETED/FAILED/...)
    |
    v
redis.hset(f"task:{task_id}", mapping={...})
redis.expire(f"task:{task_id}", 86400)
```

If the Redis write fails, no data is lost. The poll path falls back:

1. Check Redis `task:{id}` hash -- cache hit serves response immediately
2. On miss, query `query.task_query_view` in Postgres
3. If projection is stale, query `cmd.task_commands` via SQL join

Redis is an optimization layer. Its failure degrades latency, not correctness.

### Eventually consistent (projection): RabbitMQ -> PG query view

The projector consumes task events from RabbitMQ and writes to `query.task_query_view`:

```text
RabbitMQ task event
    |
    v
Projector consumer (inbox dedup)
    |
    v
INSERT/UPDATE query.task_query_view
```

Projection lag is bounded by projector throughput. During projector downtime:

- Messages accumulate in RabbitMQ (memory pressure risk -- RabbitMQ is designed for flow-through, not storage)
- Query path falls back to `cmd.task_commands` via SQL join
- No data is lost; the query view can always be rebuilt from the command table

## Dual-write status: SOLVED

The outbox pattern eliminates all dual-write gaps present in Sol 0 and Sol 1.

### Sol 0/Sol 1 dual-write problem

```text
Sol 0:  Lua deduction -> (GAP) -> PG persist -> (GAP) -> Celery/stream publish
Sol 1:  Lua deduction -> (GAP) -> PG persist -> (GAP) -> Redis stream XADD
```

Each gap required compensation logic: pending markers, reaper scans, orphan detection, refund jobs.

### Sol 2 solution

```text
Sol 2:  BEGIN -> credit deduction + reservation + task + outbox -> COMMIT
        (single store, single transaction, zero gaps)

        Then: relay -> RabbitMQ (at-least-once, outbox-guaranteed)
        Then: consumer -> inbox dedup (at-most-once processing)
```

The atomic boundary covers everything that must be consistent: billing, task state, and event publication intent. The cross-store publish (PG -> RabbitMQ) is guaranteed by the outbox relay, and the consumer inbox prevents double-processing.

**No refund jobs, no reaper scans, no orphan detection, no compensation paths for the submit flow.** The reservation timeout (watchdog) is the only background process, and it exists as a safety net for worker crashes -- not for dual-write recovery.

## Reservation model consistency

### State machine

```text
RESERVED -----> CAPTURED    (worker completes successfully)
    |
    +---------> RELEASED    (worker fails / timeout / user cancels)
```

Only these transitions are legal. Any other transition is a bug. The `state` column combined with `FOR UPDATE` row locking prevents concurrent transitions.

### Credit arithmetic

Reserve **deducts** credits immediately from `users.credits`:

```sql
UPDATE users SET credits = credits - cost WHERE user_id = $1 AND credits >= cost
```

Available credits = `users.credits` (what remains after all deductions). There is no separate "held" amount or "available vs reserved" split in the balance column. The balance IS the available amount because reservations have already been subtracted.

On release (failure/timeout/cancel), credits are added back:

```sql
UPDATE users SET credits = credits + amount WHERE user_id = $1
```

On capture (success), the reservation transitions to CAPTURED and a `credit_transactions` audit row is written. No further balance change is needed -- the deduction already happened at reserve time.

### Watchdog safety net

The watchdog releases expired reservations for tasks whose workers crashed or timed out:

```sql
SELECT reservation_id, task_id, user_id, amount
FROM credit_reservations
WHERE state = 'RESERVED' AND expires_at < now()
FOR UPDATE SKIP LOCKED
```

For each expired reservation: release credits, update task to TIMEOUT, write audit log. `SKIP LOCKED` prevents contention with concurrent workers completing the same task.

If the watchdog itself is down, expired reservations remain in RESERVED state and credits stay locked. This is a liveness issue (credits temporarily unavailable), not a correctness issue (credits are never lost). Credits are released when the watchdog recovers.

## Task state consistency

In Sol 2, **Postgres always leads**. Redis is updated after PG commit as a cache write-through. This is a structural simplification over Sol 0/Sol 1 where Redis led on submit.

```text
Submit:   PG INSERT task_commands status=PENDING   -> Redis HSET task:{id} PENDING    (PG leads)
Worker:   PG UPDATE task_commands status=RUNNING   -> Redis HSET task:{id} RUNNING    (PG leads)
Complete: PG UPDATE task_commands status=COMPLETED -> Redis HSET task:{id} COMPLETED  (PG leads)
Cancel:   PG UPDATE task_commands status=CANCELLED -> Redis HSET task:{id} CANCELLED  (PG leads)
Timeout:  PG UPDATE task_commands status=TIMEOUT   -> Redis HSET task:{id} TIMEOUT    (PG leads)
```

The poll path checks Redis first (cache hit), falls back to `query.task_query_view`, then falls back to `cmd.task_commands` join. If Redis has a stale status, the client sees stale data until the next poll. This is acceptable: status only moves forward (`PENDING -> RUNNING -> COMPLETED|FAILED|CANCELLED|TIMEOUT`), and the authoritative state is always in Postgres.

### Auth consistency

Same model as Sol 1. JWT claims are cryptographically embedded in the token. Revocation is the only eventual-consistency surface:

- **Write path:** dual-write -- Redis `SADD revoked:{uid}:{day} <jti>` + Postgres `INSERT INTO token_revocations`
- **Read path (happy):** Redis `SISMEMBER` (1 RTT, 0 DB calls)
- **Read path (Redis down):** Postgres `SELECT 1 FROM token_revocations WHERE jti=$1` fallback
- **Startup:** rehydrate today + yesterday's JTIs from Postgres into Redis
- **Cleanup:** day-partitioned table, `DROP TABLE token_revocations_YYYYMMDD` for expired partitions

## RabbitMQ consistency

### Durability

All task messages are published with `delivery_mode=2` (persistent). Queues are declared as durable. Messages survive RabbitMQ restart.

### Dead letter queue

Failed messages (consumer nack, TTL expiry, queue length overflow) route to the DLQ with a 7-day retention. DLQ messages can be inspected and replayed manually.

### Limitations

- **Consumed = gone.** Once a consumer ACKs a message, it is deleted from RabbitMQ. There is no replay capability. This is acceptable because the command table (`cmd.task_commands`) is the source of truth, not the event stream.
- **Consumer lag degrades RabbitMQ.** If the projector or webhook worker is down for an extended period, messages accumulate in memory. RabbitMQ is designed for flow-through, not storage. Extended outages require monitoring `rabbitmq_queue_depth` and alerting.
- **Query view rebuild.** The query view can always be rebuilt from the command table via SQL join. No event log is needed:

```sql
INSERT INTO query.task_query_view (task_id, user_id, tier, mode, model_class, status, result, created_at, updated_at)
SELECT task_id, user_id, tier, mode, model_class, status, NULL, created_at, updated_at
FROM cmd.task_commands
ON CONFLICT (task_id) DO UPDATE SET
  status = EXCLUDED.status,
  updated_at = EXCLUDED.updated_at;
```

## Risk register

### R1: RabbitMQ down -- tasks delayed but not lost

- **Severity:** Medium (liveness, not correctness)
- **Evidence:** Submit path writes command + reservation + outbox to Postgres in one transaction. Outbox relay cannot publish. Tasks are delayed until RabbitMQ recovers.
- **Mitigation:** Outbox rows remain with `published_at IS NULL`. Relay retries on recovery. No data loss. Monitor `outbox_unpublished_count` gauge; alert if > 50 for 2 minutes.
- **Client impact:** Submit succeeds (201). Task stays PENDING. Client sees PENDING on poll until RabbitMQ recovers and worker processes task.

### R2: Projector down -- query view stale, RabbitMQ memory pressure

- **Severity:** Medium (degraded query performance + potential broker instability)
- **Evidence:** Projector consumes task events from RabbitMQ and writes to `query.task_query_view`. When down, messages accumulate.
- **Mitigation:** Query path falls back to `cmd.task_commands` join (slower but correct). Monitor `projection_lag_seconds` and `rabbitmq_queue_depth`. If extended outage, purge projection queue and rebuild from command table via SQL.
- **Residual risk:** RabbitMQ memory pressure during extended projector outage. Broker may trigger flow control or OOM.

### R3: Redis down -- degraded latency, submit still works

- **Severity:** Low (latency degradation, not data loss)
- **Evidence:** Redis is a query cache. Submit path writes to Postgres atomically; the post-commit Redis write-through is best-effort. Poll path falls back to Postgres query view.
- **Mitigation:** Rate limit counters degrade (may temporarily allow over-limit). Task poll latency increases (PG query instead of Redis lookup). Token revocation falls back to PG lookup.
- **Client impact:** Higher latency on poll. Submit unaffected. No data loss.

### R4: Watchdog down -- expired reservations not released

- **Severity:** Medium (credits temporarily locked)
- **Evidence:** Watchdog runs `release_expired()` on a schedule. If watchdog process crashes, reservations past `expires_at` remain in RESERVED state.
- **Mitigation:** Credits are locked, not lost. When watchdog recovers, all expired reservations are released in the next scan. `FOR UPDATE SKIP LOCKED` prevents contention. Monitor `reservation_age_seconds{state="RESERVED"}` histogram; alert on p99 exceeding reservation TTL.
- **Residual risk:** Users cannot use locked credits until watchdog recovers. Severity is bounded by reservation TTL (default 10 minutes per task).

### R5: Outbox relay crash between RabbitMQ publish and marking published

- **Severity:** Low (duplicate message, not data loss)
- **Evidence:** Relay publishes to RabbitMQ, then updates `published_at`. Crash between these steps causes re-publish on restart.
- **Mitigation:** Inbox dedup at consumer prevents double-processing. `INSERT INTO inbox_events(event_id, consumer_name)` fails on duplicate PK; consumer ACKs and skips.

## Key invariants

1. **Credits are never over-charged.**

   Reserve deducts upfront. Release adds back. Capture is a no-op on balance (already deducted). On any failure mode, the reservation either captures (success) or releases (failure/timeout/cancel). The watchdog is the safety net for worker crashes. Direction is always safe: under-charge, never over-charge.

2. **Task state only moves forward.**

   `PENDING -> RUNNING -> COMPLETED | FAILED | CANCELLED | TIMEOUT -> EXPIRED`

   State transitions are guarded by `WHERE status = <expected>` predicates. Losing a race means no state overwrite. The command table is mutated via UPDATE, not appended -- this is CQRS, not event sourcing.

3. **Outbox guarantees at-least-once publish; inbox guarantees at-most-once processing.**

   Every outbox row is published to RabbitMQ at least once (relay retries unpublished). Every consumer processes each event at most once (inbox dedup on `event_id`). Combined: exactly-once semantics from the perspective of side effects.

4. **Idempotency is durable.**

   Unique constraint on `(user_id, idempotency_key)` in Postgres. Unlike Sol 0/Sol 1 where idempotency was a Redis TTL key (lost on restart), Sol 2 idempotency survives restarts and has no TTL-bounded replay window.

5. **Query view is rebuildable.**

   The query view (`query.task_query_view`) can always be rebuilt from `cmd.task_commands` via SQL. No event log or replay capability is needed. The command table is the source of truth.
