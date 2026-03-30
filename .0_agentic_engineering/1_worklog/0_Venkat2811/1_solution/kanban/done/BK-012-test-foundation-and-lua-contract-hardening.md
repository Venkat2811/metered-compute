# BK-012: Test Foundation and Lua Contract Hardening

Priority: P1
Status: done
Depends on: P1-021

## Objective

Track test-infra refinement (shared fixtures/conftest and deeper Lua contract tests) as quality hardening.

## Checklist

- [x] Consolidate duplicate unit-test fakes into shared fixtures
- [x] Add direct Lua contract tests beyond route-level integration coverage
- [x] Measure maintenance impact before/after fixture consolidation

## Acceptance Criteria

- [x] Hardening plan is actionable and scoped for a separate quality pass

## Notes

- Added shared test support module:
  - `tests/fakes.py` with reusable fake DB transaction/pool and fake Redis client/pipeline.
- Refactored duplicate fake stacks to shared module:
  - `tests/unit/test_app_paths.py`
  - `tests/fault/test_publish_failure_path.py`
- Added direct Lua runtime contract coverage:
  - `tests/integration/test_lua_contract.py`
  - validates `OK`, `IDEMPOTENT`, `CONCURRENCY`, `INSUFFICIENT`, `CACHE_MISS`
  - executes the real `ADMISSION_LUA` script against a live Redis compose service (`redis-cli EVAL`)
- Maintenance impact snapshot:
  - removed duplicate fake class blocks from two high-churn suites and centralized behavior in one module.

## Validation

- `ruff check tests/fakes.py tests/unit/test_app_paths.py tests/fault/test_publish_failure_path.py tests/integration/test_lua_contract.py`
- `pytest -q tests/unit/test_app_paths.py tests/fault/test_publish_failure_path.py tests/unit/test_lua_parser.py`
- `docker compose up -d redis && pytest -q tests/integration/test_lua_contract.py -m integration && docker compose down -v --remove-orphans`
