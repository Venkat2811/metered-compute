# P0-007: TDD Suite - Unit, Integration, E2E, Fault

Priority: P0
Status: done
Depends on: P0-006

## Objective

Finish a comprehensive automated test suite that proves functional correctness, failure handling, and reproducibility.

## Checklist

- [x] Unit tests for domain logic, validation, Lua outcomes
- [x] Integration tests for end-to-end API + Redis + Postgres + Celery behavior
- [x] E2E test for demo script flow
- [x] Fault tests:
  - [x] worker crash
  - [x] Redis down
  - [x] Postgres down
  - [x] broker publish failure path
- [x] Add coverage threshold and test markers (`unit`, `integration`, `e2e`, `fault`) split:
  - marker coverage delivered in this card
  - numeric threshold intentionally tracked in `backlog/BK-005-test-coverage-gate-70-80.md`

## TDD Subtasks

1. Red

- [x] Add failing tests for all remaining uncovered invariants

2. Green

- [x] Implement missing behavior and/or recovery until tests pass

3. Refactor

- [x] Remove flaky timing assumptions and stabilize with bounded retries/timeouts

## Acceptance Criteria

- [x] Full matrix passes consistently on local Docker Compose runs
- [x] Coverage threshold hard gate explicitly deferred to newly-added backlog card `BK-005`
- [x] Fault test outcomes align with degradation matrix claims

## Progress Notes (2026-02-15)

Implemented:

- new integration suite:
  - `tests/integration/test_api_flow.py`
- new e2e suite:
  - `tests/e2e/test_demo_script.py`
- new fault suite:
  - `tests/fault/test_readiness_degradation.py`
  - `tests/fault/test_runtime_faults.py`
  - `tests/fault/test_publish_failure_path.py`
- execution scripts:
  - `scripts/integration_check.sh`
  - `scripts/fault_check.sh`
- pytest marker registration:
  - `pyproject.toml`

Evidence:

- unit gate: `./scripts/ci_check.sh` => `19 passed`
- integration gate: `./scripts/integration_check.sh` => `7 integration passed`, `1 e2e passed`
- fault gate: `./scripts/fault_check.sh` => `4 passed` (worker, redis, postgres, publish-failure coverage)
