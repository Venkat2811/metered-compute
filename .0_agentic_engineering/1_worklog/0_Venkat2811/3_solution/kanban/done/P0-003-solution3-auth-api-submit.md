# P0-003 Solution 3 - Auth and Command API Pipeline

Objective:

Implement OAuth-backed identity, command API, and guarded task command state transitions.

Status: done on 2026-03-26 after unit, integration, and quality verification.

Acceptance criteria:

- [x] `POST /v1/task` accepts idempotent submit and writes a command row + outbox row atomically.
- [x] `GET /v1/poll` returns hot-path data.
- [x] `POST /v1/task/{id}/cancel` follows guarded state transition rules.
- [x] `POST /v1/admin/credits` requires admin role.

TDD order:

1. Add tests for each route contract before endpoint implementation:
   - submit, poll, cancel, admin credits, ownership/rbac errors.
2. Add repository command/state tests for guarded transitions.
3. Implement API adapters and route glue with minimal behavior.
4. Add integration tests with seeded users and real API stack.

Checklist:

- [x] Auth:
  - add `src/solution3/api/auth_routes.py` and auth service with Hydra/JWT verification path.
  - verify roles and scopes for admin operations.
- [x] Domain/services:
  - add `src/solution3/services/auth.py` for API key hash lookup + JWT mapping.
- [x] Submit API:
  - add `src/solution3/api/task_write_routes.py`.
  - parse idempotency key, payload, model class, requested mode.
  - write `task_commands` row using repo write helper.
- [x] Poll API:
  - add `src/solution3/api/task_read_routes.py` with Redis/query-model fallback.
- [x] Cancel/API admin:
  - add cancel contract with state guard semantics.
  - add admin credits endpoint with explicit admin scope.
- [x] Contracts:
  - add/adjust Pydantic models in `src/solution3/models/schemas.py`.
- [x] Route assembly in `src/solution3/app.py`.

Completion criteria:

- [x] Submit path writes command row + outbox row; no direct worker enqueue.
- [x] Cancel path cannot regress terminal states due to missing guard checks.
- [x] Unauthorized ownership/scopes consistently return RFC-conformant envelopes.

Verification:

- `pytest tests_bootstrap/unit`
- `pytest tests_bootstrap/integration -m integration`
- `make quality`

Notes:

- `POST /v1/admin/credits` is intentionally RBAC-only in this slice. The success path remains deferred to `P0-004`, where TigerBeetle becomes the billing source of truth.
