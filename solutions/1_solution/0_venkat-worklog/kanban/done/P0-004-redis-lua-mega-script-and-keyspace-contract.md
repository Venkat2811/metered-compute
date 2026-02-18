# P0-004: Redis Lua Mega-Script and Keyspace Contract

Priority: P0
Status: done
Depends on: P0-001, P0-002

## Objective

Implement Redis-centric admission control via a single Lua operation covering idempotency, concurrency, credit, stream enqueue, and task status seed.

## Checklist

- [x] Define key patterns and TTL policy (`credits`, `idem`, `active`, `task`, `stream`, `credits:dirty`)
- [x] Implement Lua mega-script with typed parser and `NoScriptError` reload behavior
- [x] Include cache-miss hydration/retry contract
- [x] Add companion Lua scripts for safe counter decrement and transition helpers as needed
- [x] Expose metrics for Lua latency and outcomes

## Acceptance Criteria

- [x] Admission path is one atomic Redis script call on happy path
- [x] Idempotent replay and conflict semantics are deterministic
- [x] Script behavior is covered by focused unit tests for every branch

## Progress Notes (2026-02-16, Iteration 1)

Implemented:

- Upgraded `ADMISSION_LUA` to a mega-script that now atomically performs:
  - idempotency check
  - concurrency check
  - credit deduction
  - Redis Stream enqueue (`XADD MAXLEN ~`)
  - task state seed (`HSET task:{task_id}` + `EXPIRE`)
  - idempotency TTL write + active counter increment + dirty-credit tracking
- Extended `run_admission_gate` to pass stream key, stream payload JSON, task TTL, and stream maxlen.
- Added stream/task keyspace runtime settings and defaults (`REDIS_TASKS_STREAM_KEY`, `REDIS_TASKS_STREAM_MAXLEN`, `REDIS_TASK_STATE_TTL_SECONDS`).
- Updated submit-path admission calls to pass stream payload metadata (`task_id`, `user_id`, `x`, `y`, `api_key`, `trace_id`).

TDD evidence:

- Red: introduced admission-call assertions in billing unit tests for key/argv shape.
- Green: implemented mega-script wiring and passing tests.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `make test-integration`

Completion evidence:

- Idempotent replay and conflict semantics are verified in integration contracts:
  - `tests/integration/test_api_flow.py::test_submit_poll_and_idempotent_replay`
  - `tests/integration/test_error_contracts.py::test_contract_error_codes_400_401_404_409`
- Branch-level Lua result behavior is covered via parser and billing service tests:
  - `tests/unit/test_lua_parser.py`
  - `tests/unit/test_billing_service.py`
