# 2026-02-15 Module Boundary Map and Refactor Notes

## Current boundaries

- API orchestration: `src/solution0/app.py`
- Domain models/contracts: `src/solution0/domain.py`, `src/solution0/schemas.py`
- Data access: `src/solution0/db/repository.py`
- Admission + billing primitives: `src/solution0/services/billing.py`
- Auth/cache resolution: `src/solution0/services/auth.py`
- Worker runtime: `src/solution0/worker_tasks.py`
- Recovery runtime: `src/solution0/reaper.py`
- Dependency/readiness checks: `src/solution0/dependencies.py`

## Refactor/hardening actions completed

- Added complexity gate (`scripts/complexity_gate.py`) and CI wiring (`scripts/quality_gate.sh`)
- Normalized submit-path exception mapping to deterministic `503` degradation responses
- Removed unstable hand-rolled circuit breaker from hot path to reduce incidental complexity
- Kept transactional boundaries and compensation logic explicit in API/worker/reaper

## Outstanding technical debt (explicitly tracked)

- `create_app` remains a large route-registration function by design for Solution 0
- Route module split (`routers/`) is deferred to Solution 1+ evolution where auth/routing complexity increases

## Why this is acceptable for Solution 0

- Assignment scope is fully satisfied
- Reliability semantics are explicit and test-covered
- Complexity is bounded by gate with documented overrides instead of hidden drift
