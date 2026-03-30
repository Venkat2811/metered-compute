# Solution Tracks

All tracks are designed as HA, production-ready approaches under a Docker Compose deployment constraint.

Priority order used across all RFCs:

1. Correctness
2. Reliability and HA
3. Scalability and maintainability
4. Performance and complexity

## Tracks

- `0_solution`: Pragmatic Baseline - Celery + Redis + Postgres (baseline spec, done right)
- `1_solution`: Redis-Native Engine - JWT + Redis Streams + Lua atomic pipeline
- `2_solution`: Service-Grade Platform - CQRS + RabbitMQ SLA routing + reservation billing
- `3_solution`: Financial Core - TigerBeetle + Redpanda + RabbitMQ hot/cold dispatch + CQRS projections
- `5_solution`: TB + Restate Showcase - TigerBeetle double-entry billing + Restate durable execution

Solutions 0-3 are independently excellent architectural approaches with different tradeoff profiles, each implemented with full test suites and demo scripts.
Solution 4 is the remaining RFC-only design that extends the architectural exploration without implementation.
Solution 4 is a launch decision: the best of Sol 1 (speed) + Sol 2 (correctness), minimizing infrastructure for a 2-week production ship.
Solution 5 is a compact showcase proving TigerBeetle + Restate replace thousands of lines of infrastructure code.

Solutions 0 and 5 use API key auth. Solutions 1-4 use JWT/OAuth.

## Shared documents

- `../.0_agentic_engineering/0_rfcs/` - one RFC per solution

## Observability by solution

Implemented in every coded solution: `structlog` JSON logging, `prometheus_client` metrics, Grafana dashboard.
Additional implementation in `1_solution`: optional OTel+Tempo tracing profile.
Described in RFC for later tracks: Prometheus alerting rules alignment, OpenSearch, ClickHouse.
For `3_solution`, OTel/Tempo, OpenSearch, and ClickHouse remain RFC-only options, not missing completion work.

| Solution | Implemented                                                     | Described in RFC                                     |
| -------- | --------------------------------------------------------------- | ---------------------------------------------------- |
| 0        | structlog + Prometheus + Grafana                               | Prometheus alerting rule set; alertmanager optional       |
| 1        | structlog + Prometheus + Grafana + optional OTel+Tempo profile | Prometheus alerting rule set; alertmanager optional       |
| 2        | structlog + Prometheus + Grafana                               | Prometheus alerting rule set; OpenSearch, OTel+Tempo (runtime optional) |
| 3        | structlog + Prometheus + Grafana                               | RFC-only options: OTel+Tempo, OpenSearch, ClickHouse |
| 4 (RFC)  | -                                                               | Sol 1 observability + outbox metrics                 |
| 5        | structlog + Prometheus + Grafana                               | -                                                    |

## What each solution ships as code vs. describes in RFC

| Capability                       | 0      | 1      | 2      | 3                | 4 (RFC only) | 5      |
| -------------------------------- | ------ | ------ | ------ | ---------------- | ------------ | ------ |
| Task submit/poll/cancel          | code   | code   | code   | code             | RFC          | code   |
| Credit check + deduction         | code   | code   | code   | code             | RFC          | code   |
| Auth (API key / JWT)             | code   | code   | code   | code             | RFC          | code   |
| Admin credits                    | code   | code   | code   | code             | RFC          | code   |
| Concurrency + idempotency        | code   | code   | code   | code             | RFC          | code   |
| Demo script                      | code   | code   | code   | code             | -            | code   |
| Unit + integration tests         | code   | code   | code   | code             | -            | code   |
| Scenario harness (shipped)          | 12   | 13     | 13     | 8                | -            | 13     |
| Sustained load test             | code | code   | code   | code             | -            | code   |
| Fault tests (degradation proof)  | code   | code   | code   | code             | -            | code   |
| structlog + Prometheus + Grafana | code   | code   | code   | code             | RFC          | code   |
| Prometheus alerting rules       | config | config | config | config           | RFC          | -      |
| OTel + Tempo                     | -      | optional profile | RFC    | RFC              | RFC          | -      |
| OpenSearch                       | -      | -      | RFC    | RFC              | -            | -      |
| ClickHouse OLAP                  | -      | -      | -      | RFC              | -            | -      |

## How to run

Each solution: `cd <N>_solution && docker compose up --build`

Run tests:

- Solutions 0,1,2,5: `cd <N>_solution && pytest tests/`
- Solution 3: `cd <N>_solution && pytest tests_bootstrap/`

Full verification: `cd <N>_solution && make prove`

Sustained load test: `cd <N>_solution && make loadtest` (solution-specific profiles: 0/1 and 5 default to 100 RPS × 30 s, solutions 2 and 3 use deterministic `load_harness.py`-style traffic profiles, then validate acceptance and latency)

Default demo: `0_solution` (spec-faithful, minimal containers)
Flagship demo: `1_solution` (zero-Postgres hot path, answers "reduce DB calls" directly)
CQRS demo: `2_solution` (reservation billing, outbox pattern, RabbitMQ SLA routing)
TB + Restate showcase: `5_solution` (TigerBeetle billing + Restate durable execution, ~1.8k LOC)

## Containers per solution

CQRS separation (solutions 2-3) is in the code (separate routers, separate schemas), not separate containers.

| Solution | Total | Core services                                                                          | Infrastructure                                   | Observability       |
| -------- | ----- | -------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------- |
| 0        | ~7    | api, worker, reaper                                                                    | redis, postgres                                  | prometheus, grafana |
| 1        | ~9    | api, hydra, worker, reaper, webhook-dispatcher                                         | redis, postgres                                  | prometheus, grafana |
| 2        | ~12   | api, hydra, worker, outbox-relay, projector, watchdog, webhook-worker                  | redis, postgres, rabbitmq                        | prometheus, grafana |
| 3        | ~15   | api, hydra, dispatcher, worker(s), outbox-relay, projector, reconciler, webhook-worker | redis, postgres, tigerbeetle, redpanda, rabbitmq | prometheus, grafana |
| 4 (RFC)  | ~10   | api, hydra, worker, outbox-relay, reaper, webhook-dispatcher                           | redis, postgres                                  | prometheus, grafana |
| 5        | ~8 running (+1 init) | api, compute                                                                       | redis, postgres, tigerbeetle, tb-init, restate   | prometheus, grafana |

Notes:
- Counts are long-lived services only. Startup/auxiliary one-shots are included where present:
  - `hydra-migrate`, `hydra-client-init` in solutions 1, 2, 3
  - `migrate`, `tb-init` in solution 3
  - `tb-init` in solution 5
- optional tracing profiles in solutions 1 and 2 include `tempo` + `otel-collector`; solution 3 includes `tempo` only

Solution 3 is implemented without the optional analytics profile. With ClickHouse enabled later, Sol 3 would be ~17 containers.
Solution 4 is the remaining RFC-only launch blueprint that picks Sol 1 hot path + Sol 2 outbox.
Solution 5 has 8 long-lived containers plus the one-shot `tb-init` formatter — still close to Sol 0 in operational footprint, but with TigerBeetle for billing and Restate for durable execution.

## Full comparison

| Concern               | 0 - Baseline                          | 1 - Redis-Native                                                                 | 2 - Service-Grade              | 3 - Financial Core                            | 4 - Production Launch (RFC)                     | 5 - TB + Restate                              |
| --------------------- | ------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------ | --------------------------------------------- | ----------------------------------------------- | ---------------------------------------------- |
| **Auth**              | API key + Redis cache                 | JWT + OAuth                                                                      | JWT + OAuth                    | JWT + OAuth                                   | JWT + OAuth (from Sol 1)                        | API key + Redis cache                          |
| **Queue**             | Celery + Redis                        | Redis Streams                                                                    | RabbitMQ (SLA routing)         | Redpanda + RabbitMQ dispatch                  | Redis Streams (from Sol 1)                      | Restate (durable execution)                    |
| **Credit model**      | Deduct-then-execute                   | Deduct-then-execute                                                              | Reserve/capture/release        | TB pending/post/void                          | Deduct-then-execute (Sol 1) + outbox refunds    | TB pending/post/void                           |
| **Credit atomicity**  | Redis Lua                             | Redis Lua (mega-script)                                                          | PG transaction                 | TigerBeetle (Jepsen-verified)                 | Redis Lua + PG outbox                           | TigerBeetle (Jepsen-verified)                  |
| **Dual-write risk**   | Yes (mitigated: retry + reaper)       | Reduced (Lua atomic; retry on compensation)                                      | Solved (outbox)                | Solved (outbox)                               | Solved (outbox for post-admission)              | Solved (Restate journal)                       |
| **Cancel**            | Revoke + refund                       | Redis status + refund                                                            | Release reservation            | Void pending transfer                         | PG + outbox refund                              | Void pending transfer                          |
| **Concurrency limit** | Redis INCR/DECR                       | Lua-enforced                                                                     | Reservation count              | Redis INCR/DECR                               | Lua-enforced (from Sol 1)                       | TB account flag                                |
| **Task IDs**          | UUIDv7 (app-generated)                | UUIDv7 (app-generated)                                                           | UUIDv7 (app-generated)         | UUIDv7 (app-generated, maps to TB u128)       | UUIDv7 (from Sol 1)                             | UUIDv7 (maps to TB u128)                       |
| **Idempotency**       | Redis key                             | Lua-checked                                                                      | PG unique constraint           | TB transfer ID = task ID                      | Lua-checked (from Sol 1)                        | PG unique constraint + Restate                 |
| **Webhook**           | No                                    | Optional (async POST)                                                            | RabbitMQ webhook exchange      | Redpanda webhook consumer                     | Optional (from Sol 1)                           | No                                             |
| **Batch**             | No                                    | Lua in loop                                                                      | Single reservation, fan-out    | RFC (batch endpoint documented; not implemented in shipped API) | Lua in loop (from Sol 1)                        | No                                             |
| **Queue position**    | Approximate (LLEN)                    | Approximate (XLEN; XINFO GROUPS lag/pending metrics)                            | RabbitMQ management API        | Redpanda consumer lag                         | Approximate (XLEN; XINFO GROUPS from Sol 1)     | N/A (Restate)                                  |
| **Replay/rebuild**    | No                                    | No                                                                               | No (consumed = gone)           | Yes (offset reset)                            | No (from Sol 1)                                 | Yes (Restate journal replay)                   |
| **Worker routing**    | Round-robin (Celery)                  | Round-robin (stream)                                                             | SLA queue routing              | Hot/cold model-affinity                       | Round-robin (from Sol 1)                        | Restate (single handler)                       |
| **Failure recovery**  | Reaper refund job                     | PEL + XAUTOCLAIM + refund                                                        | Watchdog releases reservations | TB auto-timeout + reconciler                  | PEL (Sol 1) + outbox relay (Sol 2) + drift audit | TB auto-timeout + Restate replay               |
| **Tiers**             | No (flat)                             | free/pro/enterprise                                                              | free/pro/enterprise            | free/pro/enterprise                           | free/pro/enterprise (from Sol 1)                | No (flat)                                      |
| **Model classes**     | No (x+y only)                         | small/medium/large                                                               | small/medium/large             | small/medium/large                            | small/medium/large (from Sol 1)                 | No (x+y only)                                  |
| **Logging**           | structlog JSON                        | structlog JSON                                                                   | structlog JSON                 | structlog JSON                                | structlog JSON (from Sol 1)                     | structlog JSON                                 |
| **Metrics**           | Prometheus client                     | Prometheus client                                                                | Prometheus client              | Prometheus client                             | Prometheus (Sol 1) + outbox metrics (Sol 2)     | Prometheus client                              |
| **Tracing**           | —                                     | Optional profile: OTel+Tempo                                                     | RFC: OTel+Tempo                | RFC-only option: OTel+Tempo                   | RFC: OTel+Tempo (from Sol 1)                    | —                                              |
| **Log search**        | —                                     | —                                                                                | RFC: OpenSearch                | RFC-only option: OpenSearch                   | — (from Sol 1)                                  | —                                              |
| **OLAP**              | —                                     | —                                                                                | —                              | RFC-only option: ClickHouse                   | — (from Sol 1)                                  | —                                              |
| **Alerting**          | Prometheus alerting rules (alertmanager optional) | Prometheus alerting rules (alertmanager optional) | Prometheus alerting rules (alertmanager optional) | Prometheus alerting rules | Prometheus alerting rules (from Sol 1, alertmanager optional) | — |
| **Dashboards**        | Grafana                               | Grafana                                                                          | Grafana                        | Grafana                                       | Grafana (from Sol 1)                            | Grafana                                        |
| **Containers**        | ~7                                    | ~9                                                                               | ~12                            | ~15                                           | ~10 (Sol 1 base + outbox-relay from Sol 2)      | ~8                                             |
| **PG on hot path**    | Auth miss only                        | Submit + worker writes (poll/auth zero-PG; revocation PG fallback if Redis down) | Command writes (txn)           | Command metadata only                         | Admission zero-PG (Sol 1); post-admission PG+outbox | Metadata only (billing in TB)                  |
| **Key strength**      | Simple, complete, spec-faithful | Zero-PG hot path                                                                 | Correct under all failures     | Financial-grade + replayable + model-affinity | Sol 1 speed + Sol 2 correctness, minimal infra  | ~1.8k LOC, Jepsen-verified billing, auto-replay |

## Cross-cutting decisions

### UUIDv7 for task IDs

All solutions generate task IDs using UUIDv7 (RFC 9562) — time-ordered UUIDs where the first 48 bits encode a millisecond timestamp. Generated application-side via Python `uuid6.uuid7()`, not Postgres `gen_random_uuid()`.

Why: UUIDv4 causes random B-tree page splits on every insert. UUIDv7 appends sequentially, eliminating write amplification on primary key indexes. `ORDER BY task_id` becomes equivalent to `ORDER BY created_at` without a separate index. Internal IDs (user_id, txn_id, event_id) retain `gen_random_uuid()` where time-ordering adds no value.

## Reproducibility

Local/dev runs use hardcoded defaults (API keys, credentials, OAuth keypair).
Production must externalize secrets and support key rotation.
