# P0-013: Readiness Worker Probe Connection Reuse

Priority: P0
Status: done
Depends on: P0-008

## Objective

Remove per-request Redis connection churn from `/ready` worker heartbeat checks and align readiness probing with pooled/shared client usage.

## Checklist

- [x] Change worker readiness probe API to accept shared Redis client and heartbeat key
- [x] Update `/ready` route to pass runtime Redis client instead of constructing a new client
- [x] Add/adjust unit tests for healthy/unhealthy worker heartbeat probe behavior
- [x] Re-run full prove gate and capture evidence

## Acceptance Criteria

- [x] `/ready` no longer creates ad-hoc Redis connections for worker probe
- [x] Unit tests cover positive and failure probe branches
- [x] `make prove` passes after probe refactor
