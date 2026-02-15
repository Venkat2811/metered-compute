donot change these:
```
Every solution is HA production ready approach. 

(1) Correctness > (2) Reliability & HA > (3) Scalability & Maintainability - System, processes & teams > (4) Performance & Complexity

(1) & (2) are guranteed in all approaches

(3) & (4) are always levels of trade-offs

RFCs discuss each approach - design goals and limitations

Each solution is runnable and tested in Mac & Linux

Observability:

docker compose of all will have the same high quality logging, oversvability and alerting stack

we will also expose business events which is searchable and analyzable from api interactions
```

can change:
# Solution Tracks

All tracks are designed as HA, production-ready approaches under a Docker Compose deployment constraint.

Priority order used across all RFCs:
1. Correctness
2. Reliability and HA
3. Scalability and maintainability
4. Performance and complexity

Tracks:
- `0_solution`: Redis + Celery + Postgres baseline
- `1_solution`: hash-key auth cache + outbox/idempotency hardening
- `2_solution`: JWT/OAuth + Redis fast-path
- `3_solution`: CQRS + RabbitMQ SLA queues
- `4_solution`: TigerBeetle + Redpanda replayable backbone

RFC index:
- `0_0_rfcs/RFC-0000-template.md`
- `0_0_rfcs/RFC-0001-0-solution-postgres-baseline.md`
- `0_0_rfcs/RFC-0002-1-solution-celery-redis-postgres.md`
- `0_0_rfcs/RFC-0003-2-solution-jwt-redis-fastpath.md`
- `0_0_rfcs/RFC-0004-3-solution-cqrs-rabbitmq-sla.md`
- `0_0_rfcs/RFC-0005-4-solution-tigerbeetle-redpanda.md`

Reproducibility policy:
- Each solution can use hardcoded local defaults (admin credentials, API keys, OAuth keypair) for deterministic local runs.
- Production uses externalized secrets and key rotation.

Observability policy:
- Every solution includes metrics, structured logs, traces, and alerting guidance.
- Business events are emitted and queryable for API and billing analysis.
