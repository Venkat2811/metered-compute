# Solution 3 - Financial Core

TigerBeetle + Redpanda + RabbitMQ hot/cold dispatch + CQRS projections

RFC: `../../0_1_rfcs/RFC-0003-3-solution-financial-core/README.md`

## Status

RFC only. No implementation planned.

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
- TigerBeetle pending/post/void two-phase transfers
- Redpanda replayable event backbone
- RabbitMQ hot/cold worker dispatch (header exchanges, preloaded/coldstart)
- Dispatcher (Redpanda → RabbitMQ bridge)
- Outbox pattern for reliable publish
- Projector for query-side materialization
- Reconciler (TB vs PG consistency)
- Webhook worker (Redpanda consumer)
- Optional ClickHouse OLAP (compose profile)
- structlog + Prometheus + Grafana
