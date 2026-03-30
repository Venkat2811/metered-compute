# P0-003: Auth Cache and Credit Lua Gate

Priority: P0
Status: done
Depends on: P0-002

## Objective

Implement authenticated task admission with Redis cache-aside auth and atomic Lua-based credit/concurrency/idempotency gate.

## Checklist

- [x] Bearer token extraction and validation against `users.api_key`
- [x] Redis cache-aside auth (`auth:<api_key>` with TTL)
- [x] Redis working-balance hydration (`credits:<user_id>`)
- [x] Lua script contract:
  - [x] idempotency check
  - [x] concurrency limit check
  - [x] balance check + deduction
  - [x] dirty-marking for snapshot/reconciliation
- [x] Cache miss retry path (`CACHE_MISS` -> PG hydrate -> retry)

## TDD Subtasks

1. Red

- [x] Add failing unit tests for Lua outcomes (`ok`, `idempotent`, `insufficient`, `concurrency`, `cache_miss`)
- [x] Add failing integration tests for auth cache hit/miss behavior

2. Green

- [x] Implement auth middleware and Lua admission gate until tests pass

3. Refactor

- [x] Extract typed gateway service for Redis script I/O and result decoding

## Acceptance Criteria

- [x] DB reads are avoided on auth cache hits and warm credit state
- [x] Admission is atomic for billing/concurrency/idempotency in Redis
- [x] Negative balance and double-charge states are impossible in tested flows

## Progress Notes (2026-02-15)

Implemented:

- auth and cache-aside:
  - `src/solution0/services/auth.py`
  - `src/solution0/db/repository.py` (`fetch_user_by_api_key`, `fetch_user_credits_by_api_key`)
- atomic admission Lua and typed parse:
  - `src/solution0/lua.py`
  - `src/solution0/app.py` (`run_admission_gate` path + cache-miss hydrate/retry)
- regression/unit tests:
  - `tests/unit/test_auth_utils.py`
  - `tests/unit/test_lua_parser.py`
- integration verification:
  - `tests/integration/test_error_contracts.py` (auth cache hit/miss metrics deltas)

Evidence:

- `./scripts/ci_check.sh` passed (`19 passed`)
- `./scripts/integration_check.sh` passed (`7 integration`, `1 e2e`)
- submit + idempotent replay in compose:
  - first submit: `201`
  - replay with same `Idempotency-Key`: `200` with same `task_id`
