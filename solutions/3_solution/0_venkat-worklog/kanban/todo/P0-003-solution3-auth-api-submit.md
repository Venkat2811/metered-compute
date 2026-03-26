# P0-003 Solution 3 - Auth and Command API Pipeline

Objective:

Implement OAuth-backed identity, command API, and guarded task command state transitions.

Acceptance criteria:

- [ ] `POST /v1/task` accepts idempotent submit and writes a command row + outbox row atomically.
- [ ] `GET /v1/poll` returns hot-path data.
- [ ] `POST /v1/task/{id}/cancel` follows guarded state transition rules.
- [ ] `POST /v1/admin/credits` requires admin role.

TDD order:

1. Add tests for each route contract before endpoint implementation:
   - submit, poll, cancel, admin credits, ownership/rbac errors.
2. Add repository command/state tests for guarded transitions.
3. Implement API adapters and route glue with minimal behavior.
4. Add integration tests with seeded users and real API stack.

Checklist:

- [ ] Auth:
  - add `src/solution3/api/auth_routes.py` and auth service with Hydra/JWT verification path.
  - verify roles and scopes for admin operations.
- [ ] Domain/services:
  - add `src/solution3/services/auth.py` for API key hash lookup + JWT mapping.
- [ ] Submit API:
  - add `src/solution3/api/task_write_routes.py`.
  - parse idempotency key, payload, model class, requested mode.
  - write `task_commands` row using repo write helper.
- [ ] Poll API:
  - add `src/solution3/api/task_read_routes.py` with Redis/query-model fallback.
- [ ] Cancel/API admin:
  - add cancel contract with state guard semantics.
  - add admin credits endpoint with explicit admin scope.
- [ ] Contracts:
  - add/adjust Pydantic models in `src/solution3/models/schemas.py`.
- [ ] Route assembly in `src/solution3/core/contracts.py` and `src/solution3/app.py`.

Completion criteria:

- [ ] Submit path writes command row + outbox row; no direct worker enqueue.
- [ ] Cancel path cannot regress terminal states due to missing guard checks.
- [ ] Unauthorized ownership/scopes consistently return RFC-conformant envelopes.
