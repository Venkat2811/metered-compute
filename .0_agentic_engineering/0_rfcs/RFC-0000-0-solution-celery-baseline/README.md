# RFC-0000: Pragmatic Baseline - Celery + Redis + Postgres

- **Status:** Implemented
- **Owner:** Venkat
- **Date:** 2026-02-15
- **Solution slot:** `0_solution`
- **Deployment constraint:** Docker Compose only

## Context and scope

This solution uses exactly the stack provided in the assignment (Celery, Redis, Postgres) and answers every requirement including the "reduce DB calls" question. No artificial limitations: Redis Lua handles atomic credit deduction even in this baseline.

Common requirements: `../../0_0_problem_statement_and_assumptions/README.md` Section A only.

What this solution delivers:

- Worker wired through Celery queue
- `/v1/task`, `/v1/poll`, `/v1/task/{id}/cancel`, `/v1/admin/credits`
- Redis Lua atomic credit deduction with concurrency enforcement
- Redis cache-aside for auth, idempotency support, refund on worker failure

What this solution does NOT include: JWT/OAuth, tiers, model classes, CQRS, outbox pattern, reservation model.

## Goals and non-goals

**Goals:** Solve the assignment completely and correctly. Demonstrate "reduce DB calls" at every level. Clean upgrade path to solutions 1-3.

**Non-goals:** Tiered SLA routing. Zero dual-write risk (acknowledged, mitigated with reaper). Financial-ledger-grade transfer model.

## The actual design

- **API:** FastAPI monolith
- **Queue:** Celery with Redis broker and Redis result backend
- **Credit deduction:** Redis Lua atomic script (check balance + deduct + enforce concurrency + set idempotency key)
- **Auth:** Bearer API key via Redis cache (TTL 60s), Postgres on miss
- **Recovery:** Reaper job for orphaned deductions, stuck tasks, result expiry
- **Containers:** ~7 (API, worker, redis, postgres, reaper, prometheus, grafana)

```text
Client
  |
  v
+-------------------------------+
| API (FastAPI)                 |
| - Auth middleware              |
| - Credit gate (Redis Lua)     |
+---------------+---------------+
                |
     +----------+----------+
     |                     |
     v                     v
+-----------+      +-----------+
| Redis     |      | Postgres  |
| credits   |      | users     |
| auth/idem |      | tasks     |
| Celery    |      | credit_txn|
+-----+-----+      +-----------+
      |                  ^
      v                  |
+----------+-------------+
| Celery Worker           |
+-------------------------+
```

## APIs

| Endpoint               | Method | Auth   | Key behavior                                              |
| ---------------------- | ------ | ------ | --------------------------------------------------------- |
| `/v1/task`             | POST   | Bearer | Submit task. 201 on success. Idempotency-Key header optional. |
| `/v1/poll`             | GET    | Bearer | Poll by task_id. Redis cache hit or PG fallback.          |
| `/v1/task/{id}/cancel` | POST   | Bearer | Guarded cancel + credit refund.                           |
| `/v1/admin/credits`    | POST   | Admin  | CTE: update balance + audit row.                          |
| `/health`, `/ready`    | GET    | None   | Liveness, readiness (Redis + PG + worker + Lua).          |

## Reducing database calls

The core question: _"Assuming the database calls are too expensive, how can we reduce the number of calls?"_

| Request    | Naive (PG only)      | This solution     | How                                                       |
| ---------- | -------------------- | ----------------- | --------------------------------------------------------- |
| **Auth**   | 1 SELECT per request | **0** (cache hit) | Redis cache-aside with 60s TTL                            |
| **Submit** | 4+ queries           | **1** PG txn      | Lua handles idempotency, concurrency, and credit in 1 RTT |
| **Poll**   | 1 SELECT per poll    | **0** (cache hit) | Worker caches result in Redis with 24h TTL                |
| **Cancel** | 2 queries            | **1** PG txn      | Postgres handles guarded transition                       |
| **Admin**  | 2 queries            | **1** CTE         | Single-statement CTE                                      |

**Typical task lifecycle** (submit -> 3 polls -> complete): Naive ~10 PG calls, this solution ~3 PG calls.

## Degradation matrix

| Component down | Submit                                    | Poll             | Admin           |
| -------------- | ----------------------------------------- | ---------------- | --------------- |
| Redis          | 503                                       | 503              | PG direct works |
| Postgres       | Cached users work; new users return 503   | Works (Redis)    | 503             |
| Celery broker  | 503 after deduct -> auto-refund           | Works (existing) | Works           |
| Workers        | Tasks queue; reaper refunds after timeout | PENDING          | Works           |

## Observability

`structlog` JSON logs + Prometheus metrics + Grafana dashboards. Alert rules in `monitoring/prometheus/alerts.yml`. See [details.md](./details.md) for metrics and alerts tables.

## Known limitations: dual-write gap

Post-admission flows write PG first, then Redis. If Redis write fails after PG commit, state is inconsistent until reaper reconciles (30-60s). Safety invariant: under-charge, never over-charge. Hardening: retry-with-backoff on all post-PG Redis ops. **Deliberate tradeoff** -- solved by outbox pattern in solution 2. See [details.md](./details.md) for per-flow gap analysis.

## Degree of constraint

- Optimized for: baseline completeness with no artificial limitations
- Tradeoff: Redis/PG cross-store durability is eventual, not transactional
- Safety invariant: billing favors under-charge over over-charge on faults
- Migration triggers: auth DB load too high -> solution 1 (JWT), need reliable publish -> solution 2 (outbox)

## Capacity model

Target: 50K customers, 30M submits/day, 150M polls/day.

| Resource         | Steady-state             | Key driver                                |
| ---------------- | ------------------------ | ----------------------------------------- |
| Postgres disk    | ~4 TB                    | `credit_transactions` with 365d retention |
| Redis memory     | ~13 GB peak              | Result cache + idempotency keys (24h TTL) |
| Network          | 144 GB/day (13 Mbps avg) | Poll traffic dominates volume             |
| Workers (`C=1`)  | 200-2,500 (elastic)      | Peak demand: 2,083 concurrent task slots  |
| API instances    | 2-3                      | Async I/O; HA floor is 2 instances        |

Full model: [Capacity Model](./capacity-model.md)

## References

- [Request Flow Diagrams](./request-flows.md)
- [Data Ownership and Consistency](./data-ownership.md)
- [Implementation Details](./details.md) (schema, metrics, alerts, hardening)
- [Capacity Model](./capacity-model.md)
- [Solution comparison matrix](../../README.md)
