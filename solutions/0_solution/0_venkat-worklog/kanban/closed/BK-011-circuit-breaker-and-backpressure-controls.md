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
