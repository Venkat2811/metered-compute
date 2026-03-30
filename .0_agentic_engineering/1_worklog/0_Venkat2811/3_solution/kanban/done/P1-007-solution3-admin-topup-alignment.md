# P1-007 Solution 3 - Admin Top-up and Docs Alignment

Objective:

Close the remaining stubbed admin top-up path so Solution 3's shipped surface matches the board, README, and RFC summary.

Acceptance criteria:

- [x] `POST /v1/admin/credits` succeeds for an authenticated admin user with the required scope.
- [x] Target user lookup uses the hashed API key table, not hardcoded settings.
- [x] TigerBeetle top-up is executed and the new balance is returned to the caller.
- [x] A command-store outbox event is recorded for successful admin top-ups.
- [x] Unit and live integration coverage prove success, not just the existing forbidden case.
- [x] Solution 3 README / board / matrix claims are re-aligned to the real shipped behavior.

TDD order:

1. Add route-level unit tests for success, not-found, and billing failure cases.
2. Add repository unit coverage for successful admin top-up outbox persistence.
3. Add a live HTTP integration test for the admin success path.
4. Implement the route and persistence path.
5. Re-run focused tests, then broader proof once the slice is green.

Checklist:

- [x] Add repository helper for admin top-up outbox persistence.
- [x] Implement Solution 3 admin top-up route using active API-key lookup + TigerBeetle top-up.
- [x] Return a useful response payload with the target API key and new balance.
- [x] Extend unit tests in `tests_bootstrap/unit/test_command_api_routes.py`.
- [x] Extend unit tests in `tests_bootstrap/unit/test_repository.py`.
- [x] Extend live integration coverage in `tests_bootstrap/integration/test_command_api_http.py`.
- [x] Align `solutions/3_solution/README.md`.
- [x] Align `solutions/README.md` if any shipped wording drift remains.

Completion criteria:

- [x] Targeted unit + integration tests pass.
- [x] `make quality` passes.
- [x] `make prove` passes from a clean state after the slice lands.

Verification notes:

- `pytest tests_bootstrap/unit/test_command_api_routes.py -q`
- `pytest tests_bootstrap/unit/test_repository.py -q`
- `pytest tests_bootstrap/integration/test_command_api_http.py -q -m integration`
- `pytest tests_bootstrap/unit/test_scenarios_script.py -q`
- `make quality`
- `make prove`
- Evidence: `worklog/evidence/full-check-20260327T152134Z`
