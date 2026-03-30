# RFC-0001: Redis-Native Engine - JWT + Redis Streams + Lua Atomic Pipeline

- **Status:** Implemented
- **Owner:** Venkat
- **Date:** 2026-02-16
- **Solution slot:** `1_solution`
- **Deployment constraint:** Docker Compose only

## Context and scope

Solution 0 established a correctness-first baseline with Celery + Redis + Postgres. Solution 1 keeps the same product surface but makes Redis the hot-path execution backbone.

What changes from solution 0:

- Auth moves to OAuth JWT flow (Hydra) with local JWT verification on request path
- Celery replaced by Redis Streams consumer groups
- Submit admission is one Lua atomic operation: idempotency + concurrency + credit + enqueue + task state write
- Poll path is Redis-first for both pending and completed tasks; Postgres is fallback
- Tier/model simulation: `free|pro|enterprise`, `small|medium|large`

What this does NOT include: outbox/inbox pattern (solution 2), reservation billing (solution 3), multi-region orchestration.

## Goals and non-goals

**Goals:** Zero PG calls on submit admission. Zero PG calls on poll happy path. Single-RTT atomic submission gate in Redis Lua. Replace Celery with Redis Streams.

**Non-goals:** Outbox/inbox exactly-once guarantees (solution 2). Reservation-ledger accounting (solution 3). Kubernetes orchestration.

## The actual design

- **API:** FastAPI with JWT auth via Hydra
- **Auth:** Hydra token issuance + local JWT verify + Redis revocation check (PG-durable day-partitioned JTI blacklist)
- **Queue/worker:** Redis Streams (`XREADGROUP` + `XAUTOCLAIM`)
- **Billing:** Redis working balances, Postgres audit/snapshots
- **Recovery:** pending-marker orphan recovery, stuck-task recovery, snapshot flush, drift audit
- **Containers:** ~9 (hydra, api, worker, reaper, webhook-dispatcher, redis, postgres, prometheus, grafana)

```text
Client
  |
  v
+---------------------------+
| API (FastAPI)             |
| - JWT verify (local)      |
| - Lua admission gate      |
+-------------+-------------+
              |
              v
+---------------------------+     +-----------------------+
| Redis                     |<--->| Worker (Streams)      |
| credits/idem/active/task  |     | XREADGROUP+XAUTOCLAIM |
| result/stream/revoked     |     | guarded PG transitions|
+-------------+-------------+     +-----------------------+
              |
              v                   +-----------------------+
+---------------------------+     | Reaper                |
| Postgres                  |<--->| orphan/stuck recovery |
| users/api_keys/tasks      |     | snapshot + drift audit|
| credit_txn/snapshots/drift|     +-----------------------+
| token_revocations (part.) |
+---------------------------+
```

## APIs

| Endpoint                        | Method | Scope            | Key behavior                                                       |
| ------------------------------- | ------ | ---------------- | ------------------------------------------------------------------ |
| `/v1/oauth/token`               | POST   | -                | API key -> JWT. Supports `api_key` or `client_id`/`client_secret`. |
| `/v1/task`                      | POST   | `task:submit`    | Lua admission gate. Model class: small/medium/large.               |
| `/v1/poll`                      | GET    | `task:poll`      | Redis-first (pending + completed). PG fallback.                    |
| `/v1/task/{id}/cancel`          | POST   | `task:cancel`    | Guarded cancel + credit refund.                                    |
| `/v1/admin/credits`             | POST   | `admin:credits`  | CTE: update balance + audit row.                                   |
| `/v1/auth/revoke`               | POST   | authenticated    | Revokes calling token (Redis + PG dual-write).                     |
| `/health`, `/ready`, `/metrics` | GET    | -                | Liveness, readiness, Prometheus metrics.                           |

## Data storage

- **Redis** owns hot-path state: credits, idempotency, queue, task status/result cache, revocation cache
- **Postgres** owns durable artifacts: users, api_keys, tasks, credit transactions/snapshots/drift, token revocations

Schema DDL, retention, Redis key patterns, and internal contracts: [details.md](./details.md)

## Reducing database calls

| Request                | Naive (PG only) | Solution 0        | **This solution**    | How                                                               |
| ---------------------- | --------------- | ----------------- | -------------------- | ----------------------------------------------------------------- |
| **Auth (per request)** | 1 SELECT        | **0** (cache hit) | **0** (zero network) | JWT local crypto; revocation uses Redis, with PG fallback         |
| **Submit**             | 4+ queries      | **1** PG txn      | **1** PG txn         | Admission + enqueue + task state are atomic in 1 Lua EVALSHA      |
| **Poll (PENDING)**     | 1 SELECT        | **1** SELECT      | **0**                | Lua writes `task:{task_id}` at submit, so pending polls hit Redis |
| **Poll (COMPLETED)**   | 1 SELECT        | **0** (cache)     | **0** (cache)        | Worker writes result cache                                        |
| **Cancel**             | 2+ queries      | **1** PG txn      | **2** PG calls       | Ownership SELECT + guarded cancel transaction                     |
| **Admin credits**      | 2 queries       | **1** CTE         | **1** CTE            | Single-statement CTE                                              |

**Typical task lifecycle** (submit -> 5 polls -> complete): Naive ~12, Sol 0 ~5, **this solution ~4 PG calls** (1 submit + 0 polls + 3 worker writes).

Key win: **poll is always zero-PG** regardless of task status.

## Degradation matrix

| Component down | Submit                            | Poll    | Admin     | OAuth             | Auth (revocation)       |
| -------------- | --------------------------------- | ------- | --------- | ----------------- | ----------------------- |
| Redis          | 503 (all hot-path)                | 503     | PG direct | JWT signing works | PG fallback (1 DB call) |
| Postgres       | Hot path works; snapshots queued  | Works   | 503       | 503 (new tokens)  | Redis cache (no change) |
| Workers        | Tasks queue in stream; PEL grows  | PENDING | Works     | Works             | N/A                     |

## Observability

`structlog` JSON logs + Prometheus metrics + Grafana dashboards. OTel Collector + Tempo available via `--profile tracing`. Metrics and alerts tables: [details.md](./details.md).

## Test posture

Unit (Lua admission, auth, billing) + integration (full stack contracts) + E2E (demo script) + fault (Redis/worker/PG degradation) + scenario (multi-user concurrency, tier/model stress, cancel-while-paused).

## Known limitations: dual-write gap

Lua admission is atomic within Redis. Post-admission flows (worker, cancel, admin) write PG first, then Redis. If Redis write fails, state is inconsistent until reaper reconciles (30-60s). Safety invariant: under-charge, never over-charge. Hardening: retry-with-backoff on all post-PG Redis ops. **Deliberate tradeoff** -- solved by outbox in solution 2. See [details.md](./details.md) for per-flow analysis and hardening list.

## Degree of constraint

- Optimized for: minimal hot-path latency and low Postgres pressure
- Safety invariant: billing favors under-charge over over-charge on faults
- Upgrade trigger to solution 2: require cross-store publish guarantees
- Upgrade trigger to solution 4: need this solution's speed with solution 2's correctness

## Capacity model

Target: 50K customers, 30M submits/day, 150M polls/day.

| Resource        | Steady-state              | Key driver                                   |
| --------------- | ------------------------- | -------------------------------------------- |
| Postgres disk   | ~4 TB (at 365d retention) | `credit_transactions` retention window       |
| Redis memory    | ~14.2 GB (elastic)        | Task hashes + result cache + idem (24h TTL)  |
| Network         | ~144 GB/day (13 Mbps avg) | Poll traffic dominates volume                |
| Workers (`C=1`) | 200-3,100 (elastic)       | Peak demand: 2,582 concurrent task slots     |
| API instances   | 2-3                       | Async I/O; HA floor is 2 instances           |

Full model: [Capacity Model](./capacity-model.md)

## References

- [Request Flow Diagrams](./request-flows.md)
- [Data Ownership and Consistency](./data-ownership.md)
- [Implementation Details](./details.md) (schema, Lua source, pseudo-code, metrics, alerts, hardening)
- [Capacity Model](./capacity-model.md)
- [Solution comparison matrix](../../README.md)
