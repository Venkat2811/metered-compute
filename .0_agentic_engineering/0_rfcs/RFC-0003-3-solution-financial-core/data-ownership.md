# RFC-0003: Data Ownership and Consistency

Parent: [RFC-0003 README](./README.md)

## Data ownership model

Solution 3 uses six stores with clear ownership boundaries. TigerBeetle is the source of truth for credit balances and transfer lifecycle. Postgres holds task metadata and CQRS projections. Redpanda provides the replayable event log. RabbitMQ handles worker dispatch. Redis is a query cache. ClickHouse stores business event OLAP data (optional).

| Data item | Primary store | Secondary store | Sync mechanism | Recovery path |
| --- | --- | --- | --- | --- |
| **User identity** | Postgres `cmd.users`, `cmd.api_keys` | - | N/A | Always in Postgres |
| **Credit balance (available)** | TigerBeetle (`credits_posted - debits_posted - debits_pending`) | - | TB is sole authority | TB is append-only ledger; balances always recoverable |
| **Credit reservations** | TigerBeetle (pending transfers, `PENDING` flag + 600s timeout) | Postgres `cmd.task_commands.tb_pending_transfer_id` | TB transfer created before PG row | Reconciler detects stale pendings; TB auto-voids on timeout |
| **Credit audit** | TigerBeetle (transfer history) | Postgres `cmd.credit_transactions` | Outbox relay | TB is permanent ledger; PG audit is supplementary |
| **Task state (command)** | Postgres `cmd.task_commands` (mutable, UPDATE in place) | - | N/A | Always in Postgres |
| **Task state (query)** | Redis `task:{id}` hash (TTL 24h) | Postgres `query.task_query_view` (projected) | Projector via Redpanda; write-through from API/worker | Cache miss -> query view -> cmd table fallback |
| **Task events (log)** | Redpanda topics (`tasks.*`, `billing.*`) | ClickHouse `events` table (optional) | Event exporter consumer | Redpanda retains 7 days; replay from offset 0 |
| **Outbox events** | Postgres `cmd.outbox_events` (`published_at NULL` = pending) | Redpanda (published copy, retained) | Outbox relay polls unpublished rows | Relay retries on restart; Redpanda retains for replay |
| **Inbox dedup** | Postgres `cmd.inbox_events` (`event_id` + `consumer_name`) | - | N/A | Always in Postgres |
| **Idempotency** | Postgres unique constraint on `(user_id, idempotency_key)` | - | N/A | Constraint survives restart |
| **Task dispatch** | RabbitMQ (header exchanges: `preloaded`, `coldstart`) | - | Dispatcher bridges Redpanda -> RabbitMQ | Redpanda retains events; dispatcher replays from last committed offset |
| **Concurrency counters** | Redis `active:{user_id}` | - | Incremented on submit; decremented on completion | Reset on restart; converges as tasks complete |
| **Warm model registry** | Redis `warm:{model_class}` set | - | Workers register on startup | Workers re-register on restart |
| **Token revocation** | Redis `revoked:{uid}:{day}` set (hot cache) | Postgres `cmd.token_revocations` (day-partitioned) | Dual-write; PG fallback on cache miss | Rehydrate Redis from PG on startup |
| **Rate limit counters** | Redis `ratelimit:*` counters (TTL-bounded) | - | N/A | Reset to 0; converges within window |
| **Projection checkpoints** | Postgres `cmd.projection_checkpoints` | - | Updated atomically with projection writes | Projector resumes from last committed offset |
| **Reconciliation jobs** | Postgres `cmd.billing_reconcile_jobs` | - | N/A | Always in Postgres |
| **Analytics events** | ClickHouse `events` table (optional profile) | - | Event exporter consumer from Redpanda | Replay from Redpanda offset 0 |

Key difference from Sol 2: **TigerBeetle is the source of truth for all credit operations.** Postgres no longer holds `users.credits` or `credit_reservations`. The application cannot overdraft even if there is a bug in the reservation logic -- TigerBeetle enforces `debits_must_not_exceed_credits` atomically.

## Stores and responsibilities

- **TigerBeetle**: account balances, pending/posted/voided transfers (source of truth for credits)
- **Postgres** (single instance, two schemas):
  - `cmd` schema: task_commands, outbox_events, inbox_events, users, api_keys, projection_checkpoints, billing_reconcile_jobs
  - `query` schema: task_query_view
- **Redis**: query cache (`task:{id}` hashes), concurrency counters (`active:{user_id}`), worker warm-model registry (`warm:{model_class}`)
- **Redpanda**: replayable event log (all domain events retained with configurable retention)
- **RabbitMQ**: worker dispatch only (hot/cold routing via header exchanges, transient -- messages are consumed and acknowledged, not retained)
- **ClickHouse**: business event OLAP (optional Compose profile, Grafana datasource, not in critical path)

## Schema DDL

### 1. Task commands (adds billing tracking vs Sol 2)

```sql
CREATE TABLE task_commands (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'async',
  model_class VARCHAR(16) NOT NULL DEFAULT 'small',
  status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
  billing_state VARCHAR(24) NOT NULL DEFAULT 'RESERVED',
  x INT NOT NULL,
  y INT NOT NULL,
  cost INT NOT NULL,
  tb_pending_transfer_id UUID NOT NULL,
  callback_url TEXT,
  idempotency_key VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`billing_state` values: `RESERVED`, `CAPTURED`, `RELEASED`, `EXPIRED`

### 2. Outbox and inbox events

```sql
CREATE TABLE outbox_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregate_id UUID NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  topic VARCHAR(128) NOT NULL,
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

### 3. Task query view (adds projection version vs Sol 2)

```sql
CREATE TABLE task_query_view (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL,
  mode VARCHAR(16) NOT NULL,
  model_class VARCHAR(16) NOT NULL,
  status VARCHAR(24) NOT NULL,
  billing_state VARCHAR(24) NOT NULL,
  result JSONB,
  error TEXT,
  runtime_ms INT,
  projection_version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

### 4. Projection checkpoints

```sql
CREATE TABLE projection_checkpoints (
  projector_name VARCHAR(64) PRIMARY KEY,
  topic VARCHAR(128) NOT NULL,
  partition_id INT NOT NULL,
  committed_offset BIGINT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5. Billing reconciliation jobs

```sql
CREATE TABLE billing_reconcile_jobs (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID NOT NULL,
  tb_pending_transfer_id UUID NOT NULL,
  state VARCHAR(24) NOT NULL DEFAULT 'PENDING',
  resolution VARCHAR(24),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6. ClickHouse events table (optional analytics profile)

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

## TigerBeetle account model

```text
Account type               | ID scheme       | flags
---------------------------|-----------------|---------------------------------
User Credit Account        | user_id mapped  | debits_must_not_exceed_credits
Platform Revenue           | fixed: 1000001  | (none)
Escrow (holds pending)     | fixed: 1000002  | (none)

Available credits = credits_posted - debits_posted - debits_pending
```

Every user gets a TB account on first auth. Seed data users get accounts created at startup.

### Transfer lifecycle

```text
  pending_transfer(user -> escrow, amount=cost, timeout=600s)
    |
    +--> post_pending_transfer   (escrow -> revenue)   = CAPTURED
    +--> void_pending_transfer   (credits auto-return) = RELEASED
    +--> timeout (auto-void)     (credits auto-return) = EXPIRED
```

This is a two-phase transfer. The pending transfer debits the user and credits escrow. Post moves escrow to revenue. Void reverses the debit. TigerBeetle enforces `debits_must_not_exceed_credits` atomically -- no application code can overdraft.

## Task ID generation

All task IDs, transfer IDs, and batch IDs are UUIDv7 (RFC 9562) generated application-side via `uuid7()`. UUIDv7 maps naturally to TigerBeetle's `u128` via `uuid_to_u128(uuid7())` -- giving TB transfer IDs implicit time ordering, which simplifies debugging and audit trail inspection (scan transfer IDs in order = chronological). Sequential B-tree inserts benefit all PG command tables. Redpanda message keys use UUIDv7, giving deterministic partition assignment with time ordering. Internal IDs (`event_id`, `job_id`) retain `gen_random_uuid()` where time-ordering adds no value.

## Index strategy

- `ux_task_cmd_user_idem` unique on `(user_id, idempotency_key)` where key not null
- `idx_task_cmd_billing_state` on `(billing_state, created_at)` where `billing_state = 'RESERVED'`
- `idx_outbox_unpublished` on `(published_at)` where `published_at IS NULL`
- `idx_task_query_user_updated` on `(user_id, updated_at DESC)`
- `idx_reconcile_state` on `(state, created_at)` where `state = 'PENDING'`
- `idx_projection_checkpoints_topic` on `(topic, partition_id)`

## Retention

- Redpanda log: 7 days (projection rebuild window)
- Redis query cache: 24h TTL
- Query view: 120 days online
- Credit transactions: 365 days
- ClickHouse events: 365 days (cheap columnar storage)
- TigerBeetle: permanent (ledger is append-only)

## Consistency boundaries

### Atomic boundary: TigerBeetle pending transfer

TigerBeetle's pending transfer is the billing atomicity boundary. The `debits_must_not_exceed_credits` flag is enforced by TigerBeetle's state machine at the storage engine level -- Jepsen-verified. No application code can overdraft, even under concurrent requests. A pending transfer atomically debits the user and credits escrow. Post/void/timeout transitions are also atomic within TB.

```text
  pending_transfer(user -> escrow, amount=cost)
    Atomic: debit user + credit escrow
    Enforced: debits_must_not_exceed_credits (Jepsen-verified)
    Timeout: 600s auto-void (credits auto-return, no application involvement)
```

### Atomic boundary: Postgres transaction

Same as Sol 2 -- command row + outbox event in one transaction:

```text
BEGIN
  1. Idempotency check     (SELECT task_id WHERE user_id + idempotency_key)
  2. Task command insert   (INSERT INTO task_commands status='PENDING', billing_state='RESERVED')
  3. Outbox event insert   (INSERT INTO outbox_events published_at=NULL)
COMMIT
```

Worker completion is also atomic within Postgres:

```text
BEGIN
  1. Task status update    (UPDATE task_commands SET status='COMPLETED', billing_state='CAPTURED')
  2. Outbox event insert   (INSERT INTO outbox_events)
COMMIT
```

All operations within a single Postgres transaction succeed or none do. No partial state.

### Cross-store boundary: TigerBeetle then Postgres with compensation

The submit path creates the TB pending transfer BEFORE the Postgres transaction. This is a cross-store write with compensation:

```text
1. TB: create pending transfer (user -> escrow)        -- can fail (insufficient credits)
2. PG: BEGIN command + outbox COMMIT                   -- can fail (idempotency, DB error)
3. If PG fails: void the pending transfer (compensate) -- credits auto-return
4. If API crashes between TB and PG: reconciler detects stale pending, TB auto-voids after timeout
```

The reconciler is the safety net for the TB-then-PG gap. It scans `task_commands` with `billing_state = 'RESERVED'` older than 12 minutes and checks TigerBeetle transfer status. If TB has auto-voided (timeout), the reconciler updates the command row to match. Credits are never permanently locked.

### Cross-store boundary: Redpanda at-least-once with inbox dedup

Outbox relay publishes events from Postgres to Redpanda with at-least-once delivery. Consumers use inbox dedup to prevent double-processing:

```text
Outbox relay:
  SELECT unpublished rows -> produce to Redpanda -> mark published
  Crash between produce and mark: re-publish on restart (duplicate)

Consumer (projector, dispatcher, webhook, exporter):
  Consume message with event_id
  INSERT INTO inbox_events(event_id, consumer_name) -- fails on duplicate PK
  If insert succeeds: process. If duplicate: ACK and skip.
```

Guarantee: every outbox event is published to Redpanda at least once and processed by each consumer at most once.

## Task state lifecycle

Task status and billing state move in lockstep. Status tracks the task lifecycle; billing state tracks the credit lifecycle in TigerBeetle.

```text
Status      | Billing State | TB Transfer State  | Transition trigger
------------|---------------|--------------------|-----------------------------------------
PENDING     | RESERVED      | pending            | Task submitted, TB pending transfer created
RUNNING     | RESERVED      | pending            | Worker picks up task from RabbitMQ
COMPLETED   | CAPTURED      | posted             | Worker succeeds, TB post_pending_transfer
FAILED      | RELEASED      | voided             | Worker fails, TB void_pending_transfer
CANCELLED   | RELEASED      | voided             | User cancels, TB void_pending_transfer
EXPIRED     | EXPIRED       | timed-out (voided) | TB 600s timeout auto-voids; reconciler updates PG
```

State transition diagram:

```text
PENDING/RESERVED ---> RUNNING/RESERVED --+--> COMPLETED/CAPTURED
                                         |
                                         +--> FAILED/RELEASED
                                         |
                                         +--> CANCELLED/RELEASED

PENDING/RESERVED ---> EXPIRED/EXPIRED  (TB timeout, reconciler aligns PG)
RUNNING/RESERVED ---> EXPIRED/EXPIRED  (TB timeout, reconciler aligns PG)
```

Status only moves forward. Transitions are guarded by `WHERE billing_state = 'RESERVED'` predicates. TigerBeetle enforces that a pending transfer can only be posted or voided once -- duplicate post/void attempts return an error, preventing double-capture or double-release at the infrastructure level.
