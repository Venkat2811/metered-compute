# P0-009: TDD Suite Unit Integration Fault E2E Load

Priority: P0
Status: done
Depends on: P0-005, P0-006, P0-007

## Objective

Build a comprehensive automated test suite proving contract correctness, concurrency behavior, and degradation handling.

## Checklist

- [x] Unit tests for auth, Lua admission parser, API routes, worker transitions, reconciler logic
- [x] Integration tests against compose stack for end-to-end submit/poll/cancel/admin
- [x] Fault tests for Redis partial failures, worker crashes, PEL growth, PG outage on snapshot paths
- [x] Concurrency tests with multi-user bursts and idempotency races
- [x] E2E demo execution tests (`demo.sh` and `demo.py`)
- [x] Coverage and complexity gates (`>=75%` global, higher for critical modules)

## Acceptance Criteria

- [x] `make prove` passes from clean environment
- [x] Tests cover key invariants in RFC-0001 critical paths
- [x] Regressions are reproducible with deterministic fixtures
