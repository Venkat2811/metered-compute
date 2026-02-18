# BK-017: Transaction Footprint and Lock Minimization Review

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Constrain transaction usage to only correctness-critical boundaries and validate lock contention/performance impact under concurrency.

## Checklist

- [x] Inventory each transaction in API/worker/reaper and classify:
  - [x] mandatory for invariants
  - [x] optional (candidate for single-statement rewrite)
- [x] Capture lock/latency evidence under stress (`pg_stat_activity`, `pg_locks`, query timing)
- [x] Identify opportunities to reduce transaction scope duration and touched rows
- [x] Add invariants/performance tradeoff notes per path in architecture review

## Exit Criteria

- [x] No unnecessary multi-statement transactions remain
- [x] Locking behavior is measured and documented
- [x] Throughput impact is quantified and acceptable for Solution 0 scope

## Evidence

- Review doc: `../../research/2026-02-15-transaction-lock-review.md`
- Runtime measurement sources:
  - `pg_stat_activity`
  - `pg_locks`
  - concurrent submit stress result (`201=3`, `429=197`)
