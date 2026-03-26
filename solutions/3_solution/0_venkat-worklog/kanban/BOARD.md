# Solution 3 - Financial Core

Last updated: 2026-03-26
Scope: `solutions/3_solution`

TigerBeetle + Redpanda + RabbitMQ hot/cold dispatch + CQRS projections

Primary references:

- `../../../0_0_problem_statement_and_assumptions/README.md`
- `../../../0_1_rfcs/RFC-0003-3-solution-financial-core/README.md`
- `../../../README.md`
- `../../../../../original-task/api_playground-master/README.md`

## Status

Implementation starts on branch `solution3-5-buildout`.

Current board shape is intentional:

- bootstrap is complete and verified
- next work starts at `P0-002` on top of a proven runtime skeleton
- sequencing and proof requirements live inside each epic checklist

## Baseline Requirements Snapshot

Solution 3 must satisfy the assignment baseline and the RFC-0003 differentiators:

- async submit / poll / cancel / admin credits API
- OAuth/JWT auth like Solutions 1-2
- TigerBeetle as billing source of truth
- Redpanda as replayable event backbone
- RabbitMQ only for worker dispatch, not as primary system-of-record queue
- CQRS query projections with replay/rebuild
- reconciler for stale pending financial state
- reviewer-first README, demo flow, scenario harness, load profile, and `make prove`

## Working Rules

- Python: `3.12.x`
- Environment manager: `uv`
- Deployment/integration harness: Docker Compose
- Development method: strict TDD (`Red -> Green -> Refactor`)
- Reuse policy: fork/adapt scaffolding from `2_solution`, but keep `3_solution` fully self-contained
- Architecture guardrail:
  - TigerBeetle owns billing correctness
  - Redpanda owns replayable event history
  - RabbitMQ owns dispatch only
  - Postgres owns metadata + query projections
  - Redis owns hot cache + warm/active registries

## Workflow

| Column         | Meaning                     |
| -------------- | --------------------------- |
| `backlog/`     | Identified, not yet scoped  |
| `todo/`        | Scoped and ready to pick up |
| `in-progress/` | Actively being worked on    |
| `done/`        | Completed and verified      |

## Card Naming

`{PRIORITY}-{ID:3d}-{kebab-case-title}.md`

Priority: `P0` (blocker), `P1` (must-have), `P2` (nice-to-have)

## Current Direction

- Build `3_solution` as an independent coded solution, not an RFC-only placeholder
- Copy/adapt proven non-architectural scaffolding from `2_solution`:
  - repo layout
  - settings/logging/metrics shape
  - test harness patterns
  - docker workflow
  - reviewer-first README and evidence flow
- Do not collapse Solution 3 into "Solution 2 plus TigerBeetle"
- Do not create a shared runtime package across solutions

## Planned Ship Order

1. bootstrap repo/runtime/tooling skeleton
2. schema/constants/contracts
3. auth + API skeleton
4. TigerBeetle billing path
5. submit path with PG + outbox
6. Redpanda relay/dispatcher/worker path
7. projector + rebuild path
8. reconciler + webhook worker
9. observability + reviewer tooling
10. full proof suite and docs alignment

## Definition Of Done

- red tests added first for each meaningful increment
- green implementation passes new and existing tests
- `ruff format`, `ruff check`, `mypy --strict` pass
- unit + integration + e2e/demo + fault tests pass for changed scope
- scenario harness and load profile are runnable
- `make prove` passes from a clean state
- README and kanban reflect actual shipped behavior

## Planned Tasks

- `done/P0-001-solution3-repo-bootstrap.md`
- `todo/P0-002-solution3-core-contracts-and-migrations.md`
- `todo/P0-003-solution3-auth-api-submit.md`
- `todo/P0-004-solution3-dispatch-worker-billing.md`
- `todo/P0-005-solution3-projections-and-recovery.md`
- `todo/P0-006-solution3-observability-proof.md`
