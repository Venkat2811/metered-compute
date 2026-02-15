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

Product capability baseline (same in all solutions):
- Subscription tiers: `free`, `basic`, `pro`, `max`, `business_pro`, `custom_contract`
- Request modes: `sync`, `async`, `batch`
- Worker simulation model classes: `small_model`, `medium_model`, `large_model`
- Cost/runtime simulation tied to model class + steps + dimensions

Tracks (approach varies, product capabilities remain available):
- `0_solution`: Foundation Runner - Redis + Celery + Postgres baseline
- `1_solution`: Secure Queue Runner - JWT + Celery + Redis + Postgres
- `2_solution`: Fastpath Engine - JWT + Redis Streams + Postgres control plane
- `3_solution`: Service Split Runner - CQRS + RabbitMQ SLA routing
- `4_solution`: Financial Core Runner - TigerBeetle + Redpanda + CQRS projections

RFC index:
- `0_0_problem_statement_and_assumptions/README.md`
- `0_1_rfcs/0_RFC-template.md`
- `0_1_rfcs/RFC-0000-0-solution-postgres-baseline.md`
- `0_1_rfcs/RFC-0001-1-solution-celery-redis-postgres.md`
- `0_1_rfcs/RFC-0002-2-solution-jwt-redis-fastpath.md`
- `0_1_rfcs/RFC-0003-3-solution-cqrs-rabbitmq-sla.md`
- `0_1_rfcs/RFC-0004-4-solution-tigerbeetle-redpanda.md`

Standard observability and analytics stack (mandatory in every solution):
- OpenTelemetry SDK instrumentation in all runtime services
- OpenTelemetry Collector as ingestion/routing layer
- Prometheus + Alertmanager for metrics and alerting
- Tempo for distributed traces
- OpenSearch for log search and operational forensics
- ClickHouse for API/business events OLAP aggregation
- Grafana dashboards over metrics, traces, logs, and OLAP views

Reproducibility policy:
- Each solution can use hardcoded local defaults (admin credentials, API keys, OAuth keypair) for deterministic local runs.
- Production uses externalized secrets and key rotation.
