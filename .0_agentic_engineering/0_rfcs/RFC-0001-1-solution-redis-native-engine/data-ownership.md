# RFC-0001: Data Ownership and Consistency

Parent: [RFC-0001 README](./README.md)

## Ownership model

Solution 1 uses a split-brain-by-design model with explicit ownership by data type:

- Redis: latency-critical mutable state and queueing.
- Postgres: durable audit/control plane and recovery anchors.

| Data type                  | Redis                                | Postgres                                   | Source of truth                            | Recovery behavior                               |
| -------------------------- | ------------------------------------ | ------------------------------------------ | ------------------------------------------ | ----------------------------------------------- |
| JWT principal              | none (claims verified locally)       | none on hot path                           | JWT claims + signature                     | N/A                                             |
| Token revocation           | `revoked:{uid}:{day}` (hot cache)    | `token_revocations` (day-partitioned)      | Postgres durable; Redis as hot cache       | rehydrate Redis from PG on startup; PG fallback |
| Credit working balance     | `credits:{uid}`                      | `credit_snapshots` + `credit_transactions` | Redis during runtime                       | rehydrated from snapshots on cache miss         |
| Idempotency                | `idem:{uid}:{key}`                   | none                                       | Redis key TTL                              | key loss allows replay after restart/TTL window |
| Active concurrency         | `active:{uid}`                       | none                                       | Redis                                      | resets on restart; converges as tasks complete  |
| Task hot state             | `task:{task_id}`                     | `tasks` table                              | Redis read-primary; PG guarded transitions | poll fallback to PG when cache missing          |
| Task terminal result cache | `result:{task_id}`                   | `tasks.result`                             | Redis cache + PG durable copy              | poll reconstructs from PG when needed           |
| Work queue                 | `tasks:stream`                       | `stream_checkpoints`                       | Redis stream                               | PEL recovery + checkpoint resume                |
| Credit dirty markers       | `credits:dirty`, `pending:{task_id}` | none                                       | Redis                                      | recreated by writes and reaper scan             |
| Drift audit                | none                                 | `credit_drift_audit`                       | Postgres                                   | durable operational history                     |

## Consistency boundaries

### Atomic boundary (strong)

The Lua admission script is atomic inside Redis:

- idempotency check
- concurrency check
- credit check + deduction
- stream enqueue (`XADD`)
- task hash write (`HSET task:{id}`)
- idempotency set
- active increment
- dirty marker add

This removes dual-write risk _within_ Redis.

### Cross-store boundary (eventual)

There is still a Redis -> Postgres durability gap after Lua succeeds and before Postgres persistence commits.

Mitigations in solution 1:

- pending marker written before Postgres persist
- compensation path on persist failure (refund + decrement active + idempotency cleanup)
- worker guarded transitions (`WHERE status IN ...`) avoid invalid state overwrite
- reaper scans pending markers and repairs orphan deductions

### Task state consistency

Redis task hashes are the **read-primary** store. Postgres owns guarded state transitions:

```text
  Submit:   Lua HSET task:{id} PENDING   ->  PG INSERT status=PENDING   (Redis leads by ~50ms)
  Worker:   PG UPDATE status=RUNNING     ->  Redis HSET task:{id}       (PG leads by ~1ms)
  Complete: PG UPDATE status=COMPLETED   ->  Redis HSET result:{id}     (PG leads by ~1ms)
  Cancel:   PG UPDATE status=CANCELLED   ->  Redis HSET task:{id}       (PG leads by ~1ms)
```

The poll path checks Redis first (two tiers: result cache, then task state), falls back to PG. If Redis has a stale status (e.g. still PENDING while PG says RUNNING), the client sees the stale status until the next poll. This is acceptable: status only moves forward, and the next poll will see the updated state from Redis or PG.

### Auth consistency

JWT auth is inherently consistent — claims are cryptographically embedded in the token. The only eventual-consistency surface is revocation.

Revocation stores only the **JTI** (JWT ID), not the full token. Since every JWT is cryptographically verified before the revocation check, the JTI is sufficient to identify a revoked token. This saves ~12x storage compared to storing full JWT strings (~36 bytes vs ~800 bytes per entry).

Revocation durability model:

- **Write path:** dual-write — Redis `SADD revoked:{uid}:{day} <jti>` (hot cache, immediate) + Postgres `INSERT INTO token_revocations` (durable).
- **Read path (happy):** Redis `SISMEMBER` pipelined check (1 RTT, 0 DB calls).
- **Read path (Redis down):** Postgres `SELECT 1 FROM token_revocations WHERE jti=$1` fallback.
- **Startup:** rehydrate today + yesterday's JTIs from Postgres into Redis.
- **Cleanup:** `token_revocations` is day-partitioned by `revoked_at`. All revocations from Tuesday are in `token_revocations_20260217`. Thursday morning: `DROP TABLE token_revocations_20260217` — instant, zero vacuum, no bloat. Partition lifecycle (create future, drop expired) is managed by `pg_partman` extension inside Postgres — no application involvement needed.

On Redis restart, revocation sets are **rehydrated from Postgres** on API startup. No revocation data is lost.

### Reducing database calls

For the per-request DB call comparison (naive vs solution 0 vs this solution), see [README.md — Reducing database calls](./README.md#reducing-database-calls).

---

## Credit refund durability risk register (solution1 scope)

### R1: API persist failure leaves Redis/PG state uncertain

- Severity: High (temporary overcharge + active-slot leakage)
- Evidence: `submit_task` DB transaction writes `tasks` + `credit_transactions` in Postgres after Redis Lua debit; compensation path is only for exceptions inside transaction (`src/solution1/api/task_write_routes.py`).
- Current mitigation:
  - single transaction around task-row + ledger write
  - rollback in all DB exceptions
  - compensation on post-transaction exception clears pending/idempotency and refunds active+credits
- Residual risk:
  - process-level crashes between DB commit and explicit Redis cleanup can still leave recovery windows

### R2: Worker failure after failure transition update but before Redis refund

- Severity: Medium (temporary credit overcharge if compensation step crashes)
- Evidence: `_handle_failure` applies `update_task_failed` + `insert_credit_transaction`, then refunds Redis in same function (`src/solution1/workers/stream_worker.py`).
- Current mitigation:
  - `update_task_failed` and `insert_credit_transaction` are in one DB transaction.
  - compensation only executes after both succeed.
  - failure path logs `stream_task_failure_db_update_failed` when DB path errors.
- Residual risk:
  - worker process crash between DB success and Redis refund may delay refund until manual/periodic actions.

### R3: Reaper stuck-task refund rollback mismatch

- Severity: High (silent under/over-refund risk under write failures)
- Evidence: `_process_stuck_tasks` updates task status and writes audit/ledger before Redis `refund_and_decrement_active` (`src/solution1/workers/reaper.py`).
- Current mitigation:
  - `should_refund` only set after both DB calls succeed.
- Residual risk:
  - DB-level failure during stuck-task loop can skip refund even when transition is uncertain.

### Promotion criteria for BK-008 active implementation

- Promote this register to active production work when any condition holds for 30 minutes:
  - `sum(rate(task_submissions_total{result="persist_failure"}[5m])) > 0.1 * sum(rate(task_submissions_total[5m]))`
  - parser-visible `stream_task_failure_db_update_failed` or `reaper_stuck_task_refund_error` events > 3/min (error log count)
  - `sum(rate(reaper_refunds_total{reason="stuck_task"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`
  - `sum(rate(reaper_refunds_total{reason="orphan_marker"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`
- Follow-up actions when triggered:
  - design and validate a durable outbox for credit ledger + active token reconciliation
  - add bounded replay scanner for failed `refund_and_decrement_active` writes
  - add explicit metrics for compensation-failed paths and alerting

## Invariants

1. Credits are never over-charged.

- On uncertain failure, compensation always moves toward refund/under-charge.

2. State transitions are monotonic and guarded.

- `PENDING -> RUNNING -> COMPLETED|FAILED`
- `PENDING|RUNNING -> CANCELLED`
- transition updates use guarded predicates; losing race means no state overwrite.

3. Idempotency is scoped per user.

- Redis key scope and Postgres uniqueness scope are both `(user_id, idempotency_key)`.

4. Authorization on request path is zero-DB when Redis is up.

- JWT claims + local signature verification + Redis revocation check (1 pipelined RTT).
- On Redis failure: Postgres `token_revocations` fallback (1 DB call).

## Failure examples

### API crash after Lua admission, before Postgres insert

- Redis has deducted credit and queued stream task.
- Pending marker exists or is missing depending on crash point.
- Reaper and compensation logic restore credit when durable task row is absent.

### Worker crash after claiming stream entry

- Entry remains in PEL.
- `XAUTOCLAIM` by another worker reclaims idle entry.
- Guarded transitions prevent duplicate terminal updates.

### Redis restart

- Hot state may be lost.
- Poll path falls back to Postgres.
- Credit cache rehydrates from snapshots/users on next submit attempt.
- Revocation sets are rehydrated from `token_revocations` table on API startup. No revocation data lost.
