# RFC-0004: Solution 4 -- TigerBeetle + Restate Showcase

- **Status:** Implemented
- **Owner:** Venkat
- **Date:** 2026-02-17
- **Solution slot:** `4_solution`
- **Deployment constraint:** Docker Compose only

## Context and scope

Solutions 0-3 are full implementations (800-3000+ LOC of application Python, depending on track). Solution 4 takes a different approach: a compact standalone implementation (~1.8k lines of Python) that demonstrates how TigerBeetle (Jepsen-verified double-entry accounting) and Restate (durable execution framework) can replace thousands of lines of infrastructure code while providing stronger correctness guarantees.

This is not a production evolution of Solutions 0-2. It is a focused showcase proving that the right infrastructure choices can eliminate entire categories of application-level complexity.


### What this solution replaces

Solutions 0-2 require extensive application-level infrastructure to achieve reliability and correctness:

- **Outbox tables + relay services** for reliable cross-system publish
- **Inbox dedup tables** for exactly-once consumer processing
- **Watchdog/reaper services** for timeout-based credit release
- **Credit reservation SQL** with `FOR UPDATE` locking and state machine transitions
- **Credit transaction log tables** for audit
- **Compensation code** in every failure branch

Solution 4 replaces all of these with two purpose-built systems: TigerBeetle handles billing correctness, and Restate handles durable execution. The application code shrinks to routing, auth, and orchestration.

### What this solution delivers

- All four endpoints: `/v1/task`, `/v1/poll`, `/v1/task/{id}/cancel`, `/v1/admin/credits`
- TigerBeetle pending/post/void transfer lifecycle for billing
- Restate durable workflow with journaled steps for task execution
- External compute service so Restate stays on the control plane
- Redis cache for auth and task status
- Postgres for metadata only (users, API keys, tasks)
- Prometheus + Grafana observability
- Quality gate (mypy strict, bandit, pip-audit, detect-secrets, radon complexity)
- Coverage gate with per-module floors
- Integration tests against the live stack
- Fault tests for compute outage and immediate credit release
- Scenario harness (13 scenarios including multi-user concurrency and metrics checks)

## Problem statement

In Solutions 0-2, billing correctness and crash recovery are the responsibility of application code. This creates several problems:

1. **Credit reservation logic is fragile.** One missed `WHERE state='RESERVED'` clause and credits leak. One missing `FOR UPDATE` and concurrent requests double-spend.
2. **The outbox pattern works but is heavyweight.** It requires an outbox table, a relay service, an inbox dedup table, and careful transaction boundaries at every publish point.
3. **Watchdog/reaper services are operational burden.** They run on timers, require monitoring, and add failure modes of their own.
4. **Compensation code is scattered.** Every failure branch must manually reverse the credit deduction, update the task status, and invalidate the cache -- in the correct order.
5. **Testing coverage must span all failure combinations.** Crash between step 2 and step 3? Crash between step 4 and step 5? Every gap is a potential bug.

The core question: can purpose-built infrastructure eliminate these categories of bugs entirely, rather than testing around them?

## Proposal

Replace application-level billing and reliability infrastructure with two systems designed for those problems:

- **TigerBeetle** replaces all credit SQL, reservation tables, transaction log tables, and watchdog services. It enforces double-entry invariants at the storage engine level.
- **Restate** replaces the outbox table, relay service, inbox dedup, and manual compensation code. It journals each workflow step and replays from the last completed step on crash.

Postgres and Redis remain but with narrower roles: Postgres stores metadata (users, API keys, task rows), and Redis caches auth lookups and task status for fast polling.

## Architecture

### Containers (8 long-lived + 1 one-shot init)

| Container    | Image                             | Role                              |
| ------------ | --------------------------------- | --------------------------------- |
| `api`        | Custom (FastAPI)                  | HTTP routes + Restate service     |
| `compute`    | Custom (FastAPI)                  | External compute worker           |
| `restate`    | `docker.restate.dev/restatedev/restate:1.6.2` | Durable execution runtime        |
| `tigerbeetle`| `ghcr.io/tigerbeetle/tigerbeetle:0.16.78`     | Double-entry accounting engine    |
| `tb-init`    | `ghcr.io/tigerbeetle/tigerbeetle:0.16.78`     | One-shot: format data file        |
| `postgres`   | `postgres:16`                     | Metadata store (users, keys, tasks) |
| `redis`      | `redis:7-alpine`                  | Auth and task status cache        |
| `prometheus` | `prom/prometheus:latest`          | Metrics collection                |
| `grafana`    | `grafana/grafana:latest`          | Dashboards                        |

The API container mounts the Restate service endpoint as an ASGI sub-app at `/restate`. Restate calls back into this endpoint to execute workflow steps. Compute is already split into a separate `compute` container so the Restate control plane does not own data-plane execution.

### System-context diagram

```text
Client
  |
  +-- POST /v1/task --------+
  +-- GET  /v1/poll --------+---> API (FastAPI)
  +-- POST /v1/task/cancel -+      |  \
  +-- POST /v1/admin/credits+      |   +-- /restate (ASGI sub-app)
                                   |          ^
                                   |          | (callback)
                                   |          |
                                   |     +---------+
                                   |     | Restate |
                                   |     +----+----+
                                   |          |
                            +------+------+    |
                            |      |      |    |
                            v      v      v    |
                         +----+ +-----+ +----------+
                         | PG | |Redis| |TigerBeetle|
                         +----+ +-----+ +----------+
                                   |
                                   v
                             +-----------+
                             | Compute   |
                             | Worker    |
                             +-----------+

+-----------+  +---------+
| Prometheus|  | Grafana |
+-----------+  +---------+
```

## What TigerBeetle replaces

| Solution 0-2 component            | Solution 4 replacement                               |
| --------------------------------- | ---------------------------------------------------- |
| `credit_reservations` table       | TB pending transfers (auto-timeout after 300s)        |
| `credit_transactions` table       | TB built-in transfer log (queryable via lookup)       |
| Watchdog/reaper service           | TB auto-void on pending transfer timeout              |
| Credit arithmetic SQL with locks  | `debits_must_not_exceed_credits` account flag         |
| Reconciler service                | TB is the source of truth -- no reconciliation needed |
| `WHERE state='RESERVED' FOR UPDATE` guards | Atomic transfer state machine inside TB      |

### TigerBeetle account model

```text
Account type               | ID scheme      | Flags
---------------------------|----------------|----------------------------------
User credit account        | user UUID → u128 | DEBITS_MUST_NOT_EXCEED_CREDITS
Platform revenue           | fixed: 1000001 | (none)
Escrow (holds pending)     | fixed: 1000002 | (none)

Available balance = credits_posted - debits_posted - debits_pending
```

Every user gets a TB account on first interaction. Seed data users get accounts at startup with initial balances transferred from the revenue account.

### Transfer lifecycle

```text
pending_transfer(user → escrow, amount=cost, timeout=300s)
  |
  +--> post_pending_transfer   → CAPTURED (escrow → revenue)
  +--> void_pending_transfer   → RELEASED (credits auto-return to user)
  +--> timeout (auto-void)     → EXPIRED  (credits auto-return to user)
```

The pending transfer atomically debits the user and credits escrow. Post moves escrow to revenue. Void reverses the debit. TigerBeetle enforces `debits_must_not_exceed_credits` at the engine level -- application code cannot overdraft regardless of bugs in the request handling path.

## What Restate replaces

| Solution 0-2 component     | Solution 4 replacement                            |
| --------------------------- | ------------------------------------------------- |
| `outbox_events` table       | Restate journal (durable step log)                |
| Outbox relay service        | Built-in retry and delivery (Restate runtime)     |
| `inbox_events` dedup table  | Built-in idempotency (keyed by invocation ID)     |
| Worker compensation code    | Lifecycle replay from last step (control plane only) |
| Manual cache sync on failure| Durable step: `ctx.run()` journals the cache write   |

### Control plane vs data plane

Restate manages the **task lifecycle** (control plane): mark running, capture credits, store result, update cache. It does NOT run inference (data plane).

The shipped implementation already dispatches compute to a separate `compute` service over HTTP via `workers/compute_gateway.py`. That keeps Restate handlers lightweight and avoids coupling durable execution to long-running inference workloads.

The journaling benefit applies to the control plane steps (credit capture, result storage) — these are the operations that need exactly-once guarantees. Inference is inherently retriable and should run on specialized compute infrastructure.

### Durable workflow

The task execution workflow is a Restate service handler:

```python
@task_service.handler()
async def execute_task(ctx: restate.Context, request: dict) -> dict:
    task_id = request["task_id"]
    tb_transfer_id = int(request["tb_transfer_id"], 16)

    # Control: mark running (idempotent -- safe to replay)
    await repository.update_task_status(pool, task_id, "RUNNING")

    # Data plane: compute result via external worker
    result = await ctx.run(
        "compute",
        lambda: request_compute_sync(...),
    )

    # Control: capture credits in TigerBeetle (journaled -- money safe)
    captured = await ctx.run("capture_credits",
                             lambda: billing.capture_credits(tb_transfer_id))

    # Control: store result + update cache (idempotent)
    await repository.update_task_status(pool, task_id, "COMPLETED", result=result)
    await cache.cache_task(redis_conn, task_id, {...})
```

Each `ctx.run()` call is journaled by Restate. If the process crashes between step 2 and step 3, Restate replays the handler and skips steps 1-2 (already completed), then resumes from step 3. No manual compensation code. No outbox. No retry logic.

## Data model

### Postgres tables (3 total)

```sql
-- 1. Users (metadata only -- balance is in TigerBeetle)
CREATE TABLE users (
    user_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(128) NOT NULL,
    credits    INT NOT NULL DEFAULT 0,  -- read-only mirror of TB balance
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. API keys
CREATE TABLE api_keys (
    key_hash  VARCHAR(64) PRIMARY KEY,
    user_id   UUID NOT NULL REFERENCES users(user_id),
    is_active BOOLEAN NOT NULL DEFAULT true
);

-- 3. Tasks
CREATE TABLE tasks (
    task_id         UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    status          VARCHAR(24) NOT NULL DEFAULT 'PENDING',
    x               INT NOT NULL,
    y               INT NOT NULL,
    result          JSONB,
    cost            INT NOT NULL,
    tb_transfer_id  VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Compare this to Solution 2 which requires 7 tables (`task_commands`, `credit_reservations`, `credit_transactions`, `outbox_events`, `inbox_events`, `users`, `api_keys`) plus the `task_query_view` projection table.

For full data ownership model, consistency boundaries, and degradation matrix, see [Data Ownership and Consistency](./data-ownership.md).

## Request flows

Summary of per-endpoint store call counts:

| Endpoint       | PG calls | TB calls | Redis calls | Restate calls |
| -------------- | -------- | -------- | ----------- | ------------- |
| Submit         | 1        | 1        | 1           | 1             |
| Workflow       | 2        | 1        | 1           | 0             |
| Poll (hit)     | 0        | 0        | 1           | 0             |
| Cancel         | 2        | 1        | 1           | 0             |
| Admin credits  | 1        | 2        | 0           | 0             |

Typical task lifecycle (submit + 5 polls + complete): ~4 PG, ~2 TB, ~7 Redis. Compare Sol 0 naive: ~12 PG calls.

For detailed per-endpoint sequence diagrams, see [Request Flow Diagrams](./request-flows.md).

## Comparison table

| Dimension            | Solution 0 (Celery)        | Solution 2 (CQRS+Outbox)       | Solution 4 (TB+Restate)        |
| -------------------- | -------------------------- | ------------------------------- | ------------------------------ |
| Python LOC           | ~800                       | ~3000+                          | ~700-900                       |
| PG tables            | 4                          | 8                               | 3                              |
| Compose containers   | ~7                         | ~12                             | 8                              |
| Outbox/relay         | None (dual-write risk)     | Yes (outbox + relay + inbox)    | None (Restate journal)         |
| Billing correctness  | App SQL + reaper           | App SQL + reservation FSM       | TB engine-level invariant      |
| Crash recovery       | Reaper job (delayed)       | Outbox replay + watchdog        | Restate auto-replay (instant)  |
| Watchdog needed      | Yes (reaper)               | Yes (reservation timeout)       | No (TB auto-void)              |
| Compensation code    | Manual in every branch     | Manual in every branch          | None (Restate replays)         |
| Credit overdraft     | Possible (app bug)         | Possible (app bug)              | Impossible (TB flag)           |

## Trade-offs

### Advantages

- **Correctness by construction.** TigerBeetle's `debits_must_not_exceed_credits` flag makes overdraft impossible at the storage engine level. No amount of application bugs can violate this invariant.
- **Dramatic reduction in application code.** ~700-900 LOC vs ~3000+ LOC for Solution 2, with stronger guarantees. Fewer lines = fewer bugs = less maintenance.
- **No operational services for reliability.** No outbox relay to monitor, no watchdog to tune, no inbox dedup table to clean up. Restate and TigerBeetle handle these concerns internally.
- **Crash recovery is automatic.** Restate replays from the last journaled step. No manual compensation code, no retry tuning, no dead-letter queues.

### Risks and costs

- **TigerBeetle is a runtime dependency.** It is a Zig-based system that is not yet widely adopted. The team must operate and monitor a new storage engine. However, it is Jepsen-verified and designed for exactly this use case.
- **Restate is a runtime dependency.** Built by the co-creators of Apache Flink, it is a Rust-based single binary with sub-10ms per-step overhead. The team must learn the journal+replay execution model, which differs from traditional request-response debugging — Restate provides a UI for inspecting invocation state. It delivers the same durable execution guarantees as Temporal with significantly less operational overhead and fits naturally in Docker Compose.
- **Vendor coupling.** Both TB and Restate are specific products, not patterns. The outbox pattern in Solution 2 works with any message broker. Restate journals are Restate-specific.
- **Debugging model differs.** Restate replays are deterministic but the execution model (journal + replay) differs from traditional request-response debugging. Restate provides a dashboard for inspecting invocation state.
- **Single-node TigerBeetle.** In Docker Compose, TB runs as a single replica. Production deployment requires cluster configuration for durability.

## Alternatives considered

### Fork Solution 2 and add TB + Restate

Rejected. Forking Solution 2 and swapping in TB + Restate would demonstrate the integration but would not demonstrate the LOC reduction. The showcase value is in the contrast: building from scratch with the right infrastructure is dramatically simpler than adding reliability patterns to a traditional stack.

### Temporal instead of Restate

Temporal provides the same durable execution guarantees but requires a cluster (Temporal server + Cassandra or Postgres backing store + multiple services). Restate is a single Rust binary with sub-10ms per-step overhead and fits naturally in Docker Compose. For a showcase focused on simplicity, Restate is the better choice.

### PostgreSQL advisory locks instead of TigerBeetle

Advisory locks can prevent concurrent credit deductions, but they do not solve double-entry accounting. They do not provide `debits_must_not_exceed_credits` as a storage-level invariant. They do not auto-void pending reservations on timeout. They do not provide a queryable transfer log. TigerBeetle solves the actual problem, not just the concurrency symptom.

### Keep application-level outbox alongside Restate

Rejected. The entire point of Restate is to eliminate the outbox pattern. Keeping both would add complexity without benefit. The Restate journal IS the outbox -- it just happens to also handle retry, dedup, and compensation automatically.

## Observability

### Metrics

| Metric                          | Type      |
| ------------------------------- | --------- |
| `task_submitted_total{status}`  | counter   |
| `task_completed_total`          | counter   |
| `task_cancelled_total`          | counter   |
| `task_failed_total`             | counter   |
| `credit_reserved_total`         | counter   |
| `credit_captured_total`         | counter   |
| `credit_released_total`         | counter   |
| `credit_topup_total`            | counter   |
| `http_request_duration_seconds` | histogram |

### Health and readiness

- `GET /health` -- always returns 200 if the process is alive
- `GET /ready` -- checks Postgres and Redis connectivity
- `GET /metrics` -- Prometheus scrape endpoint

## Test posture

Unit (billing, cache, repository, settings, logging, workflows, monitoring assets) + integration (health, submit+poll, cancel, admin credits, metrics surfaces) + fault tests (compute outage + immediate release) + scenario harness (13 scenarios: auth, idempotency, insufficient credits, cancel refund, ownership enforcement, multi-user concurrency, metrics, demo).

Quality gate: ruff format, ruff check, mypy --strict, bandit, pip-audit, detect-secrets, radon complexity/maintainability.

Coverage gate: global floor 35% (app.py is a FastAPI factory only testable via integration), critical module floors: billing ≥ 70%, cache ≥ 80%, repository ≥ 80%.

Single-command verification: `make prove` (6 phases: quality → coverage → compose up → integration → fault → 13 scenarios).

## Capacity model

Target: 50K customers, 30M submits/day. Key findings:

- PG writes ~3,180/sec peak (vs ~5,600 in Sol 2) — 43% reduction because billing is in TB
- Redis memory ~1.3 GB (vs ~14 GB in Sol 0/1) — 90% reduction because billing state is in TB
- TB ops ~2,124/sec peak (<0.3% of TB capacity)
- Restate ~5,200 journal entries/sec (well within single-node capacity)

For full capacity model with per-component breakdown and scaling triggers, see [Capacity Model](./capacity-model.md).

## Degree of constraint

- **Optimized for:** minimal LOC, correctness by infrastructure, showcase value
- **Pattern:** durable execution + double-entry engine (not CQRS, not event sourcing, not outbox)
- **Not production-ready for:** tiered SLA routing, batch submission, webhook delivery, multi-model dispatch. These are covered in Solutions 2-4.
- **Showcase thesis:** TigerBeetle + Restate eliminate ~70% of application infrastructure code while providing strictly stronger correctness guarantees than any amount of application-level testing.

## References

- [Request Flow Diagrams](./request-flows.md)
- [Data Ownership and Consistency](./data-ownership.md)
- [Capacity Model](./capacity-model.md)
- [TigerBeetle documentation](https://docs.tigerbeetle.com/)
- [Restate documentation](https://docs.restate.dev/)
- [Sol 0 RFC](../RFC-0000-0-solution-celery-baseline/README.md)
- [Sol 2 RFC](../RFC-0002-2-solution-service-grade-platform/README.md)
- [Sol 3 RFC](../RFC-0003-3-solution-financial-core/README.md)
- `../../README.md`
