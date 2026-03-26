# P0-001 Solution 5 - Security, Authz, and Readiness Hardening

Objective:

Close correctness and authorization gaps in the current Sol 5 showcase without changing the TB + Restate thesis.

Acceptance criteria:

- [ ] Poll endpoint enforces authenticated ownership checks.
- [ ] Admin credits endpoint enforces admin role.
- [ ] State transitions in repository are guarded.
- [ ] Readiness includes Restate and TigerBeetle checks.

TDD order:

1. Add failing tests for ownership, RBAC, transition guards, and readiness behavior.
2. Implement one minimal code change per test to keep blast radius low.
3. Run impacted test group and refactor shared helpers if repeated.

Checklist:

- [ ] App/Auth:
  - add token/user claim extraction on poll path.
  - enforce caller user id matches task user.
- [ ] Admin security:
  - require role from auth principal for `/v1/admin/credits`.
- [ ] Repository safety:
  - change update methods to include expected-status guards.
  - return transition outcome and avoid silent overwrite.
- [ ] Workflow handoff:
- [ ] replace fire-and-forget submission path with durable error-return semantics.
- [ ] if workflow invoke fails, API returns explicit server state error and performs deterministic compensation.
- [ ] Readiness:
  - add TigerBeetle connectivity probe.
  - add Restate connectivity probe + clear degradation code path.
- [ ] Update errors:
  - preserve envelope shape and use `TASK_NOT_FOUND`, `FORBIDDEN`, `UNAVAILABLE` where applicable.

Completion criteria:

- [ ] Security and readiness tests pass at API boundary.
- [ ] No state transition can occur without explicit guard.
