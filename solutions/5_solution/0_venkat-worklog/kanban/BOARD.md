# Solution 5 - TB + Restate Showcase

Last updated: 2026-03-26
Scope: `solutions/5_solution`

TigerBeetle double-entry billing + Restate durable execution

Primary references:

- `../../../0_0_problem_statement_and_assumptions/README.md`
- `../../../0_1_rfcs/RFC-0005-5-solution-tb-restate-showcase/README.md`
- `../../../README.md`
- `../../../../../original-task/api_playground-master/README.md`

## Status

Showcase implementation exists. Service-grade expansion starts on branch `solution3-5-buildout`.

Current board shape is intentional:

- keep one expansion epic in `todo/`
- fix correctness/hardening before adding feature surface
- only split into smaller cards later if the implementation proves that a single epic is too coarse

## Baseline Requirements Snapshot

Solution 5 currently demonstrates the TB + Restate thesis. The next phase must decide how far it goes toward Solution 2 parity while remaining honest to the Solution 5 stack:

- preserve TigerBeetle as billing authority
- preserve Restate as durable control-plane orchestrator
- avoid reintroducing Solution 2 machinery unless clearly justified
- close current correctness/authorization gaps
- broaden feature/test/ops surface enough that the solution is no longer just a minimal showcase

## Working Rules

- Python: `3.12.x`
- Environment manager: `uv`
- Deployment/integration harness: Docker Compose
- Development method: strict TDD (`Red -> Green -> Refactor`)
- Architecture guardrail:
  - TigerBeetle owns billing truth
  - Restate owns orchestration and timers
  - Postgres owns metadata/query state
  - Redis remains cache and lightweight coordination only
- No "secret Sol 2 rewrite" with RabbitMQ/outbox/CQRS unless the RFC direction itself changes

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

- Preserve the core thesis of Solution 5:
  - TigerBeetle for billing correctness
  - Restate for durable workflow/control plane
- Treat the current implementation as a strong prototype, not the final service-grade answer
- Harden the existing system first, then expand feature surface and proof depth

## Planned Ship Order

1. baseline correctness and authorization hardening
2. durable submit/workflow handoff redesign
3. external compute-plane extraction
4. product-surface expansion where justified
5. observability and operator experience
6. rigorous proof suite and doc alignment

## Definition Of Done

- red tests added first for each meaningful increment
- green implementation passes new and existing tests
- `ruff format`, `ruff check`, `mypy --strict` pass for changed scope
- unit + integration + e2e/demo + fault tests pass for changed scope
- scenario harness and load validation reflect the new behavior
- `make prove` passes from a clean state
- README/RFC alignment notes match shipped behavior

## Planned Tasks

- `todo/P0-001-solution5-hardening-auth-readiness.md`
- `todo/P0-002-solution5-restate-external-compute.md`
- `todo/P0-003-solution5-service-surface-proof.md`
