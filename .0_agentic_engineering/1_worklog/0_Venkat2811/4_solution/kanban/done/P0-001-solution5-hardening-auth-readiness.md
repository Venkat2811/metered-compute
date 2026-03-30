# P0-001 Solution 4 - Security, Authz, and Readiness Hardening

Status: DONE (with deterministic handoff compensation)

Objective:

Close correctness and authorization gaps in the current Sol 5 showcase without changing the TB + Restate thesis.

Acceptance criteria:

- [x] Poll endpoint enforces authenticated ownership checks.
- [x] Admin credits endpoint enforces admin role.
- [x] State transitions in repository are guarded.
- [x] Readiness includes Restate and TigerBeetle checks.

TDD order:

1. Add failing tests for ownership, RBAC, transition guards, and readiness behavior.
2. Implement one minimal code change per test to keep blast radius low.
3. Run impacted test group and refactor shared helpers if repeated.

Checklist:

- [x] App/Auth:
  - add token/user claim extraction on poll path.
  - enforce caller user id matches task user.
- [x] Admin security:
  - require role from auth principal for `/v1/admin/credits`.
- [x] Repository safety:
  - use guarded transitions for handoff rollback.
  - return transition outcome and avoid blind overwrite.
- [x] Workflow handoff:
  - replace fire-and-forget submission path with guarded compensation semantics.
  - if workflow invoke fails after task creation, API either transitions `PENDING -> FAILED` atomically with credit release or returns current terminal/in-flight state.
- [ ] Readiness:
  - add TigerBeetle connectivity probe.
  - add Restate connectivity probe + clear degradation code path.
- [ ] Update errors:
  - preserve envelope shape and use `TASK_NOT_FOUND`, `FORBIDDEN`, `UNAVAILABLE` where applicable.

Completion criteria:

- [ ] Security and readiness tests pass at API boundary.
- [ ] No state transition can occur without explicit guard.
