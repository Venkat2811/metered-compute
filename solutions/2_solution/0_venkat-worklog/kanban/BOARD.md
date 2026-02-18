# Solution 2 - Service-Grade Platform

CQRS + RabbitMQ SLA routing + reservation billing

RFC: `../../0_1_rfcs/RFC-0002-2-solution-service-grade-platform/README.md`

## Workflow

| Column         | Meaning                     |
| -------------- | --------------------------- |
| `backlog/`     | Identified, not yet scoped  |
| `todo/`        | Scoped and ready to pick up |
| `in-progress/` | Actively being worked on    |
| `done/`        | Completed and verified      |

## Card naming

`{PRIORITY}-{ID:3d}-{kebab-case-title}.md`

Priority: `P0` (blocker), `P1` (must-have), `P2` (nice-to-have)

## Definition of done

- Code compiles and passes lint
- Unit tests pass
- Integration tests pass (docker compose up)
- Demo script runs end-to-end
- Fault tests demonstrate graceful degradation

## Scope

- FastAPI with CQRS routers (cmd/ + query/) + OAuth service
- RabbitMQ with SLA queue routing + DLQ
- Reserve/capture/release billing (PG transactional)
- Outbox pattern for reliable publish
- Projector for query-side materialization
- Watchdog for expired reservations
- Webhook worker (RabbitMQ exchange)
- structlog + Prometheus + Grafana

## Current Status

First-pass scaffold and submit path complete (P1-001 through P1-009).
Core runtime paths are complete end-to-end (P0-010 through P0-014).
Cleanup pass is complete (`P1-015` through `P1-020` complete).
P2 backlog features (`P2-021`, `P2-022`, `P2-023`) are implemented and verified.
Review reconciliation pass (`P2-024`) is complete (docs/runbook parity aligned to code).

## P0 — Architecture Fixes (must-do, broken or stub)

| Card | Title | Depends on |
| ---- | ----- | ---------- |
| P0-010 | Worker — RabbitMQ consumer and task execution | P1-006, P1-008 |
| P0-011 | Projector — query view materializer | P1-006, P1-008 |
| P0-012 | Watchdog — reservation expiry and cleanup | P1-008 |
| P0-013 | Fix cancel path — reservation release and PG-native refund | P1-008 |
| P0-014 | Fix poll path — query view + cmd join read model | P0-011 |

## P1 — Dead Code Removal and Cleanup

| Card | Title | Depends on |
| ---- | ----- | ---------- |
| P1-015 | Remove Sol 1 billing dead code | P0-013 |
| P1-016 | Remove Sol 1 Redis key patterns | P0-013, P0-014 |
| P1-017 | Fix admin credits — add outbox event | P1-008 |
| P1-018 | RabbitMQ readiness check | P1-006 |
| P1-019 | Clean dead settings, imports, and docstrings | P1-015, P1-016 |
| P1-020 | Remove Sol 1 repository dead functions | P0-010, P0-013, P0-014 |

## P2 — Backlog (nice-to-have)

No open P2 backlog cards.

## Completed (First Pass)

1. `done/P1-001-solution2-fork-and-scaffold.md`
2. `done/P1-002-solution2-cmd-query-schema.md`
3. `done/P1-003-solution2-domain-and-routing-contracts.md`
4. `done/P1-004-solution2-auth-parity.md`
5. `done/P1-005-solution2-submit-reservation-path.md`
6. `done/P1-006-solution2-outbox-relay.md`
7. `done/P1-007-solution2-domain-constants-and-routes.md`
8. `done/P1-008-solution2-repository-cmd-query-layer.md`
9. `done/P1-009-solution2-submit-reservation-flow.md`
10. `done/P0-010-worker-rabbitmq-consumer-and-execution.md`
11. `done/P0-011-projector-query-view-materializer.md`
12. `done/P0-012-watchdog-reservation-expiry-and-cleanup.md`
13. `done/P0-013-fix-cancel-path-reservation-release.md`
14. `done/P0-014-fix-poll-path-query-view-and-cmd-join.md`
15. `done/P1-015-remove-sol1-billing-dead-code.md`
16. `done/P1-016-remove-sol1-redis-key-patterns.md`
17. `done/P1-017-fix-admin-credits-outbox-event.md`
18. `done/P1-018-rabbitmq-readiness-check.md`
19. `done/P1-019-clean-dead-settings-and-docstrings.md`
20. `done/P1-020-sol1-repository-dead-functions.md`
21. `done/P2-023-reservation-and-queue-depth-metrics.md`
22. `done/P2-021-batch-submit-endpoint.md`
23. `done/P2-022-sync-execution-mode.md`
24. `done/P2-024-review-reconciliation-doc-and-runbook-parity.md`

## Ship Order (Recommended)

1. **P0-010** Worker — unblocks task execution
2. **P0-011** Projector — unblocks query view population
3. **P0-012** Watchdog — unblocks reservation safety net
4. **P0-013** Cancel path fix — correct reservation lifecycle
5. **P0-014** Poll path fix — correct read model (needs P0-011)
6. **P1-015** Remove billing dead code (needs P0-013)
7. **P1-016** Remove Redis key patterns (needs P0-013, P0-014)
8. **P1-017** Admin credits outbox event
9. **P1-018** RabbitMQ readiness check
10. **P1-019** Dead settings cleanup (needs P1-015, P1-016)
11. **P1-020** Repository dead functions (needs P0-010, P0-013, P0-014)
