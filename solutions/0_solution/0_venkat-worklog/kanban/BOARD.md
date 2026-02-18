# Solution 0 Kanban Board

Last updated: 2026-02-17
Scope: `solutions/0_solution` (Pragmatic Baseline)

Primary references:

- `../../../0_0_problem_statement_and_assumptions/README.md` (Section A)
- `../../../0_1_rfcs/RFC-0000-0-solution-celery-baseline/README.md`
- `../../../../original-task/api_playground-master/README.md`

## Baseline Requirements Snapshot

This track must ship all assignment requirements with production-grade engineering quality:

- Async `POST /v1/task` and `GET /v1/poll`
- Bearer auth against pre-populated `users.api_key`
- Credit check/deduction before task acceptance
- Admin credit update endpoint
- Worker execution through Celery
- Redis + Postgres + Docker Compose baseline stack
- Explicit DB-call reduction strategy (Redis cache-aside + Lua gate)

## Working Rules

- Python: `3.12.x`
- Environment manager: `uv` (`uv venv`, `uv sync`)
- Deployment and integration harness: Docker Compose
- Development method: strict TDD (`Red -> Green -> Refactor`)
- Type safety: no untyped business logic in core paths (`mypy --strict` gate)

## Dependency Snapshot (Python 3.12 Compatible, Online-Verified)

See: `../research/2026-02-15-python312-dependency-matrix.md`

Core versions selected for this track:

- `fastapi==0.129.0`
- `uvicorn[standard]==0.40.0`
- `celery[redis]==5.6.2`
- `redis==6.4.0` (latest compatible with `celery[redis]==5.6.2`)
- `asyncpg==0.31.0`
- `pydantic==2.12.5`
- `pydantic-settings==2.13.0`
- `structlog==25.5.0`
- `prometheus-client==0.24.1`
- `uuid6==2025.0.1` (UUIDv7 generation)

## Lanes

- `todo/`: ready for pickup
- `in-progress/`: active work
- `done/`: finished and validated with evidence
- `closed/`: explicitly rejected/deferred for this solution scope
- `backlog/`: not required for Solution 0 scope, future hardening

## Current Status

P0 scope for Solution 0 is complete and validated.

Post-P0 completed cards:

1. `done/BK-005-test-coverage-gate-70-80.md`
2. `done/BK-006-code-quality-standard-and-lint-stack.md`
3. `done/BK-008-makefile-developer-workflow.md`
4. `done/BK-015-lua-bootstrap-and-redis-startup-contract.md`
5. `done/BK-017-transaction-footprint-and-lock-minimization-review.md`
6. `done/BK-007-architecture-and-best-practices-review.md`
7. `done/BK-010-connection-pooling-and-resource-lifecycle-hardening.md`
8. `done/BK-013-graceful-sigterm-and-shutdown-drills.md`
9. `done/BK-014-logging-contract-and-trace-context-hardening.md`
10. `done/BK-001-load-profile-and-capacity-model.md`
11. `done/BK-002-opentelemetry-tempo-upgrade-path.md`
12. `done/BK-003-production-ha-packaging.md`
13. `done/BK-004-clean-code-refactoring-hardening.md`
14. `done/BK-009-rate-limit-and-concurrency-stress.md`
15. `done/BK-012-transactional-uow-and-rollback-audit.md`
16. `done/BK-018-high-throughput-consistency-patterns-evaluation.md`
17. `done/BK-019-task-id-uuidv7-migration.md`
18. `done/BK-020-worker-runtime-and-loop-model-hardening.md`
19. `done/BK-021-readiness-uses-shared-pool-and-timeouts.md`
20. `done/BK-022-auth-cache-contract-cleanup.md`
21. `done/BK-023-docker-reproducible-builds-with-uv-lock.md`
22. `done/BK-024-bug-fixes-triage-followups.md`

Closed/rejected cards:

1. `closed/BK-011-circuit-breaker-and-backpressure-controls.md` (closed_rejected)

Completed P0 cards:

1. `done/P0-000-worklog-bootstrap-and-dependency-research.md`
2. `done/P0-001-repo-bootstrap-and-quality-gates.md`
3. `done/P0-002-schema-migrations-and-seed-data.md`
4. `done/P0-003-auth-cache-and-credit-lua-gate.md`
5. `done/P0-004-task-api-contracts-and-error-taxonomy.md`
6. `done/P0-005-celery-worker-cancel-and-reaper.md`
7. `done/P0-006-observability-prometheus-grafana-and-structured-logs.md`
8. `done/P0-007-tdd-suite-unit-integration-e2e-fault.md`
9. `done/P0-008-demo-script-and-release-readiness.md`

## Immediate Next

No active cards. Post-P0 hardening tranche is complete. Query timeout hardening (statement_timeout, socket_timeout, jitter) was shipped as part of earlier hardening cards.

## P2 Backlog

No remaining cards.

## Definition of Done (Per Card)

- Red tests added first and observed failing
- Green implementation passes new and existing tests
- Refactor keeps tests green and reduces complexity
- `ruff check` passes
- `mypy --strict` passes
- Unit/integration tests pass for impacted paths
- Card contains evidence commands + outcomes

## Release Readiness Gates (Solution 0)

All must pass:

- Functional: assignment endpoints and auth/billing behavior
- Correctness: no double-charge; refunds on terminal failure/cancel/orphan
- Reliability: degraded behavior follows RFC degradation matrix
- Operability: `/health`, `/ready`, `/metrics`, Prometheus + Grafana up
- Reproducibility: one-command demo script completes end-to-end

## Artifact Layout

- Runbook: `../RUNBOOK.md`
- Baseline templates/gates: `../baselines/`
- Research notes: `../research/`
- Kanban cards: this directory
