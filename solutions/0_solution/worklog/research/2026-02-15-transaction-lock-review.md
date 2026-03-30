# Transaction Footprint and Lock Review (BK-017)

Date: 2026-02-15  
Scope: `solutions/0_solution`

## Transaction Inventory

Mandatory transactional boundaries (invariant-preserving):

1. Submit persist path (`app.py`)
   - `create_task_record` + `insert_credit_transaction(task_deduct)` in one DB transaction.
   - Why mandatory: prevent task row without matching billing audit row.
2. Publish-failure compensation path (`app.py`)
   - `update_task_failed` + `insert_credit_transaction(publish_refund)` in one DB transaction.
   - Why mandatory: keep failure state and refund audit coupled.
3. Cancel path (`app.py`)
   - `update_task_cancelled` + `insert_credit_transaction(cancel_refund)` in one DB transaction.
   - Why mandatory: prevent cancellation without refund record.
4. Worker terminal failure path (`worker_tasks.py`)
   - `update_task_failed` + `insert_credit_transaction(failure_refund)` in one DB transaction.
   - Why mandatory: prevent failed task state without refund audit.
5. Reaper stuck-task recovery (`reaper.py`)
   - `update_task_failed` + `insert_credit_transaction(stuck_refund)` in one DB transaction.
   - Why mandatory: preserve recovery idempotence and auditability.
6. Admin credits (`db/repository.py`)
   - `UPDATE users` + `INSERT credit_transactions` in one DB transaction.
   - Why mandatory: avoid balance/audit divergence.
7. Migrations (`db/migrate.py`)
   - each migration file + schema_migrations insert in one DB transaction.
   - Why mandatory: avoid partially applied schema versions.

Optional/candidate simplifications:

- None identified that preserve the same invariants with less complexity in this scope.
- Existing multi-step transactions already have short critical sections and single-user row touch patterns.

## Lock and Activity Evidence

Load command (200 concurrent submit attempts, `xargs -P20`):

```bash
seq 1 200 | xargs -P20 curl POST /v1/task ...
```

Observed HTTP status distribution:

- `201`: 3
- `429`: 197

Interpretation:

- Admission gate enforced per-user concurrency early in Redis.
- Most pressure is rejected before DB writes, reducing lock pressure.

`pg_stat_activity` samples during load:

- mostly `idle` sessions
- 1 `active` backend at sample points
- no persistent lock waits captured

`pg_locks` samples during load:

- no sustained blocking lock sets
- transient `virtualxid / ExclusiveLock` seen only momentarily

## Findings

1. Transaction footprint is narrow and correctness-oriented.
2. DB write concurrency is naturally bounded by Redis admission (`max_concurrent=3`).
3. No evidence of lock contention hotspots under this Solution 0 stress profile.

## Recommendations

1. Keep current transaction boundaries as-is for correctness.
2. If throughput target increases materially, first scale via worker count and admission policy before relaxing DB invariants.
3. Revisit single-statement patterns only after benchmark evidence shows transaction overhead dominates.
