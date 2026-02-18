# Solution 1 Kanban Board

Last updated: 2026-02-17 (single hardening task in progress)
Scope: `solutions/1_solution` (Redis-Native Engine)

Primary references:

- `../../../0_0_problem_statement_and_assumptions/README.md` (Sections A + B)
- `../../../0_1_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`
- `../../../README.md` (solution matrix + code vs RFC scope)
- `../../../../../original-task/api_playground-master/README.md`

## Baseline Requirements Snapshot

This track must ship all assignment requirements and solution-1 differentiators:

- Async `POST /v1/task` and `GET /v1/poll`
- `POST /v1/task/{id}/cancel` and `POST /v1/admin/credits`
- Off-the-shelf OAuth provider (`Ory Hydra`, Go) plus `/v1/oauth/token` adapter endpoint
- JWT validation in API with local signature verification on hot path
- Redis Lua mega-script for idempotency + concurrency + credit deduction + stream enqueue + task status write
- Redis Streams consumer groups replacing Celery; PEL recovery and stuck entry claiming
- Zero-Postgres hot path on submit/poll happy path
- Reconciler for snapshots, drift audit, and recovery
- Structured logs + Prometheus + Grafana (implemented)
- Alertmanager and OTel/Tempo included as config/RFC scope, not mandatory compose runtime for baseline demo

## Working Rules

- Python: `3.12.x`
- Environment manager: `uv` (`uv venv --python 3.12 .venv`, `uv sync --frozen`)
- Deployment and integration harness: Docker Compose
- Development method: strict TDD (`Red -> Green -> Refactor`)
- Type safety: strict typing in domain/service/repository contracts (`mypy --strict` gate)
- Reproducibility: one-command clean + build + full verification (`make full-check`)

## Reuse Policy (0_solution -> 1_solution)

- Strategy: fork-and-diverge with explicit reuse boundary.
- Copy/adapt scaffolding from `../0_solution` where architecture-agnostic:
  - logging contracts and helpers
  - metrics module structure
  - Dockerfile and compose conventions
  - Makefile and quality/test gate workflow
  - test layout and evidence scripts
- Do not create shared libraries across solutions.
- Replace architecture-specific logic end-to-end:
  - auth: API key cache -> Hydra OAuth/JWT
  - queue: Celery -> Redis Streams
  - workers/cancel/recovery: revoke + reaper -> consumer groups + PEL recovery + reconciler
  - data plane: PG-backed task states -> Redis-native task state hashes on hot path
  - capability surface: add tiers/model classes in this solution

## Lanes

- `todo/`: scoped and ready for implementation
- `in-progress/`: active work
- `done/`: completed and validated with evidence
- `closed/`: explicitly rejected or deferred out of solution-1 scope
- `backlog/`: follow-up hardening beyond P0 delivery

## Current Status

- `P0-000` is complete.
- `P0-001` is complete.
- `P0-002` is complete.
- `P0-003` is complete.
- `P0-004` is complete.
- `P0-005` is complete.
- `P0-006` is complete.
- `P0-007` is complete.
- `P0-008` is complete.
- `P0-009` is complete.
- `P0-010` is complete.
- `P0-011` is complete.
- `P0-012` is complete.
- `P0-013` is complete.
- `P0-014` is complete.
- `P1-015` is complete.
- `P1-016` is complete.
- `P1-017` is complete.
- `P1-018` is complete.
- `P1-027` is complete.
- `P1-022` is complete.
- `P1-021` is complete.
- `P1-030` is complete.
- `P1-031` is complete.
- `P1-032` is complete.
- `BK-012` is complete.
- `BK-009` is complete.
- `BK-002` is complete.
- `BK-003` is complete.
- `BK-005` is complete.
- Small tactical fixes from `P1-023` and `P1-024` are merged into `P1-021`.
- `BK-013` is complete as the single hardening task for this tranche.
- Query timeout hardening (statement_timeout, socket_timeout, jitter) was shipped as part of earlier hardening cards.

## P2 Backlog

No remaining cards.

## P1 Hardening Tranche (Completed)

1. `P1-015-jwt-only-auth-and-route-scope-enforcement`
2. `P1-016-stream-orphan-recovery-and-task-state-coherence`
3. `P1-017-observability-contract-metrics-alerts-and-cardinality`
4. `P1-018-ops-hardening-doc-consistency-and-runtime-safety`

## Post-Review Remediation Tranche (Agreed Ship Order)

1. `done/P1-027-tooling-and-scenario-auth-path-correction.md`
2. `done/P1-022-stream-reclaim-policy-and-runtime-safety.md`
3. `done/P1-021-spec-alignment-submit-contract-model-cost-and-worker-warmup.md`
4. `done/P1-030-rfc0001-folder-restructure-and-doc-reconciliation.md`
5. `done/P1-031-consolidated-final-hardening-pass.md` (includes clean-state `make prove`)
6. `done/P1-032-pg-durable-jti-revocation-blacklist.md` (PG-durable revocation + fallback + rehydration)
7. `done/BK-013-bug-fixes-triage-followups.md` (single consolidated hardening checklist)

## P0 Delivery Sequence

1. `done/P0-000-worklog-bootstrap-and-dependency-research.md`
2. `done/P0-001-repo-bootstrap-and-solution0-scaffold-fork.md`
3. `done/P0-002-schema-migrations-and-seed-templates.md`
4. `done/P0-003-hydra-oauth-jwt-auth-and-revocation-contracts.md`
5. `done/P0-004-redis-lua-mega-script-and-keyspace-contract.md`
6. `done/P0-005-task-api-contracts-submit-poll-cancel-admin.md`
7. `done/P0-006-stream-worker-consumer-group-and-pel-recovery.md`
8. `done/P0-011-dual-publish-cutover-and-celery-decommission.md`
9. `done/P0-012-jwt-hot-path-revocation-retention-and-token-errors.md`
10. `done/P0-007-reconciler-snapshot-drift-and-expiry-jobs.md`
11. `done/P0-008-observability-metrics-dashboard-and-events.md`
12. `done/P0-009-tdd-suite-unit-integration-fault-e2e-load.md`
13. `done/P0-010-demo-and-release-readiness.md`
14. `done/P0-013-readiness-worker-probe-connection-reuse.md`
15. `done/P0-014-tier-model-concurrency-stress-hardening.md`

## Definition of Done (Per Card)

- Red tests added first and observed failing
- Green implementation passes new and existing tests
- Refactor keeps tests green and improves maintainability
- `ruff format`, `ruff check`, `mypy --strict` pass
- Security/quality gates pass for changed scope
- Evidence commands and outcomes are recorded in card notes

## Release Readiness Gates (Solution 1)

All must pass:

- Functional: OAuth token flow + submit/poll/cancel/admin contracts
- Correctness: no double refund/deduction under races; idempotency scoped correctly
- Reliability: degradation behavior follows RFC-0001 matrix
- Operability: `/health`, `/ready`, `/metrics`, Prometheus + Grafana up
- Recovery: PEL recovery + reconciler cycles validated by fault tests
- Reproducibility: one command clean-to-green verification + demo script evidence
