# BK-012: Transactional UoW and Rollback Audit

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Formalize unit-of-work boundaries for all multi-statement billing/task transitions and verify rollback semantics under injected faults.

## Checklist

- [x] Document transaction boundaries for submit/cancel/publish-failure/worker-failure/reaper-recovery
- [x] Add targeted tests that inject mid-transaction failures and assert rollback
- [x] Add idempotent write safeguards for repeated failure-retry paths
- [x] Add query-level lock strategy review for race-prone transitions

## Exit Criteria

- [x] Every multi-step mutation path is atomic or explicitly compensating by design
- [x] Rollback behavior is tested, not inferred
- [x] No partial-write billing states remain in verified paths

## Evidence

- Audit: `../../research/2026-02-15-transaction-uow-rollback-audit.md`
- Related lock review: `../../research/2026-02-15-transaction-lock-review.md`
