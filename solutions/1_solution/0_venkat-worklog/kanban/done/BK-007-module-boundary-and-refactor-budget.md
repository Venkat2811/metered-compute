# BK-007: Module Boundary and Refactor Budget

Priority: P2
Status: done
Depends on: P0-010

## Objective

Continuously control codebase complexity with explicit module boundaries and refactor budget.

## Refactor-Budget Plan

### Phase-1 (implemented now)

- Extract reusable API error envelope construction into `solution1.api.error_responses`.
- Replace duplicated local route-level wrappers in `solution1/api/task_write_routes.py`,
  `solution1/api/task_read_routes.py`, and `solution1/api/admin_routes.py`.
- Add focused regression tests for envelope shape and optional retry metadata.

### Phase-2 (deferred)

- Split route-side authorization/read-path helpers and cache/task-state helpers into
  dedicated request-context modules.
- Introduce module-level package ownership map and complexity budget ownership in
  `scripts/complexity_gate.py` for the entire `api` package.
- Add architecture ADR for boundary conventions + module ownership documentation.

## Implementation Outcomes

- Added `solution1/api/error_responses.py` with `api_error_response`.
- Route modules now use shared helper directly instead of local `_error_response` wrappers.
- Kept behavior unchanged (same status codes and error envelope fields), proven by
  focused unit tests + route behavior tests.

## Checklist

- [x] Extract shared API error-response helper module
- [x] Use shared helper in multiple route modules (task write/read/admin)
- [ ] Set file/function complexity thresholds per package
- [ ] Refactor oversized modules into cohesive service/repository/api units
- [ ] Keep strict typing and test coverage stable during refactor

## Acceptance Criteria

- [x] No behavior change in status/error payload for touched routes
- [ ] Complexity gates are enforced in quality checks
- [ ] Module ownership and boundaries are clearly documented

## Validation

- `python3 -m py_compile src/solution1/api/error_responses.py src/solution1/api/task_write_routes.py src/solution1/api/task_read_routes.py src/solution1/api/admin_routes.py tests/unit/test_error_responses.py`
- `uv run pytest -q tests/unit/test_error_responses.py tests/unit/test_app_paths.py::test_submit_rejects_oversized_idempotency_key tests/unit/test_app_paths.py::test_submit_reject_paths`
