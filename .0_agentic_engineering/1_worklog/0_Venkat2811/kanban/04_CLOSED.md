# metered-compute Kanban — Closed

Last updated: 2026-03-30

### S0-BK-011-circuit-breaker-and-backpressure-controls
- Solution: Sol0

# BK-011: Circuit Breaker and Backpressure Controls

Priority: Backlog
Status: closed_rejected
Depends on: P0-008

## Objective

Introduce explicit dependency-level circuit breakers and backpressure policies for Redis/Postgres/Celery paths to avoid retry storms and cascading failure.

## Resolution

Closed/rejected for Solution 0 after implementation trial and rollback.

Reason:

- introduced additional failure branches and operational complexity in this baseline
- no RFC0 requirement to ship breaker state machines
- deterministic degradation (`503`) + compensation/reaper paths remain in place

## Retained outcomes

- dependency degradation behavior is still tested
- backpressure/concurrency controls are covered by load/fault scenarios
- breaker evaluation notes retained for future solution tracks

## Evidence

- Evaluation: `../../research/2026-02-15-circuit-breaker-evaluation.md`
- Fault coverage: `tests/fault/test_readiness_degradation.py`, `tests/fault/test_runtime_faults.py`

### S1-P1-023-poll-terminal-consistency-and-fallback-correctness
- Solution: Sol1

# P1-023: Poll Terminal Consistency and Fallback Correctness

Priority: P1
Status: closed
Depends on: P1-018

## Closure Reason

Merged into `P1-021-spec-alignment-submit-contract-model-cost-and-worker-warmup` as a small tactical fix.

## Note

This item remains in scope for implementation, but no longer warrants a standalone card.

### S1-P1-024-idempotency-canonicalization-and-header-boundaries
- Solution: Sol1

# P1-024: Idempotency Canonicalization and Header Boundaries

Priority: P1
Status: closed
Depends on: P1-018

## Closure Reason

Merged into `P1-021-spec-alignment-submit-contract-model-cost-and-worker-warmup` as a small boundary guard change.

## Note

This item remains in scope for implementation, but no longer warrants a standalone card.

### S1-P1-029-doc-rfc-readme-assumption-reconciliation
- Solution: Sol1

# P1-029: Documentation Reconciliation (RFC, Matrix, Assumptions)

Priority: P1
Status: closed
Depends on: P1-019, P1-020, P1-021, P1-022, P1-023, P1-024, P1-025, P1-026, P1-027

## Closure Reason

Absorbed into `P1-030-rfc0001-folder-restructure-and-doc-reconciliation` to avoid duplicate doc churn.

## Note

Doc reconciliation is still required and tracked under P1-030.

