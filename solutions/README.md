# Solution Tracks

All tracks are designed as HA, production-ready approaches under a Docker Compose deployment constraint.

Priority order used across all RFCs:

1. Correctness
2. Reliability and HA
3. Scalability and maintainability
4. Performance and complexity

## Tracks

- `0_solution`: Pragmatic Baseline — Celery + Redis + Postgres
- `1_solution`: Redis-Native Engine — JWT + Redis Streams + Lua atomic pipeline
- `2_solution`: Service-Grade Platform — CQRS + RabbitMQ SLA routing + reservation billing
- `3_solution`: Financial Core — TigerBeetle + Redpanda + RabbitMQ hot/cold dispatch + CQRS projections
- `4_solution`: TB + Restate Showcase — TigerBeetle double-entry billing + Restate durable execution

Solutions 0-3 are independently excellent architectural approaches with different tradeoff profiles, each implemented with full test suites and demo scripts.
Solution 4 is a compact showcase proving TigerBeetle + Restate replace thousands of lines of infrastructure code with ~1.8k LOC.

Solutions 0 and 4 use API key auth. Solutions 1-3 use JWT/OAuth.

## Shared documents

- `../.0_agentic_engineering/0_rfcs/` — one RFC per solution

## Observability by solution

Implemented in every solution: `structlog` JSON logging, `prometheus_client` metrics, Grafana dashboard.
Additional in `1_solution`: optional OTel+Tempo tracing profile.
For `3_solution`, OTel/Tempo, OpenSearch, and ClickHouse remain RFC-only options.

| Solution | Implemented                                                     | Described in RFC                                     |
| -------- | --------------------------------------------------------------- | ---------------------------------------------------- |
| 0        | structlog + Prometheus + Grafana                               | Prometheus alerting rule set; alertmanager optional       |
| 1        | structlog + Prometheus + Grafana + optional OTel+Tempo profile | Prometheus alerting rule set; alertmanager optional       |
| 2        | structlog + Prometheus + Grafana                               | Prometheus alerting rule set; OpenSearch, OTel+Tempo (runtime optional) |
| 3        | structlog + Prometheus + Grafana                               | RFC-only options: OTel+Tempo, OpenSearch, ClickHouse |
| 4        | structlog + Prometheus + Grafana                               | —                                                    |

## What each solution ships

| Capability                       | 0      | 1      | 2      | 3                | 4      |
| -------------------------------- | ------ | ------ | ------ | ---------------- | ------ |
| Task submit/poll/cancel          | code   | code   | code   | code             | code   |
| Credit check + deduction         | code   | code   | code   | code             | code   |
| Auth (API key / JWT)             | code   | code   | code   | code             | code   |
| Admin credits                    | code   | code   | code   | code             | code   |
| Concurrency + idempotency        | code   | code   | code   | code             | code   |
| Demo script                      | code   | code   | code   | code             | code   |
| Unit + integration tests         | code   | code   | code   | code             | code   |
| Scenario harness (shipped)       | 12     | 13     | 13     | 8                | 13     |
| Sustained load test              | code   | code   | code   | code             | code   |
| Fault tests (degradation proof)  | code   | code   | code   | code             | code   |
| structlog + Prometheus + Grafana | code   | code   | code   | code             | code   |
| Prometheus alerting rules        | config | config | config | config           | —      |
| OTel + Tempo                     | —      | optional profile | RFC    | RFC      | —      |
| OpenSearch                       | —      | —      | RFC    | RFC              | —      |
| ClickHouse OLAP                  | —      | —      | —      | RFC              | —      |

## How to run

Each solution: `cd <N>_solution && docker compose up --build`

Run tests:

- Solutions 0, 1, 2, 4: `cd <N>_solution && pytest tests/`
- Solution 3: `cd 3_solution && pytest tests_bootstrap/`

Full verification: `cd <N>_solution && make prove`

Sustained load test: `cd <N>_solution && make loadtest`

Default demo: `0_solution` (pragmatic baseline, minimal containers)
Flagship demo: `1_solution` (zero-Postgres hot path)
CQRS demo: `2_solution` (reservation billing, outbox pattern, RabbitMQ SLA routing)
TB + Restate showcase: `4_solution` (TigerBeetle billing + Restate durable execution, ~1.8k LOC)

## Containers per solution

CQRS separation (solutions 2-3) is in the code (separate routers, separate schemas), not separate containers.

| Solution | Total | Core services                                                                          | Infrastructure                                   | Observability       |
| -------- | ----- | -------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------- |
| 0        | ~7    | api, worker, reaper                                                                    | redis, postgres                                  | prometheus, grafana |
| 1        | ~9    | api, hydra, worker, reaper, webhook-dispatcher                                         | redis, postgres                                  | prometheus, grafana |
| 2        | ~12   | api, hydra, worker, outbox-relay, projector, watchdog, webhook-worker                  | redis, postgres, rabbitmq                        | prometheus, grafana |
| 3        | ~15   | api, hydra, dispatcher, worker(s), outbox-relay, projector, reconciler, webhook-worker | redis, postgres, tigerbeetle, redpanda, rabbitmq | prometheus, grafana |
| 4        | ~8 (+1 init) | api, compute                                                                    | redis, postgres, tigerbeetle, tb-init, restate   | prometheus, grafana |

Notes:
- `hydra-migrate`, `hydra-client-init` in solutions 1, 2, 3
- `migrate`, `tb-init` in solution 3
- `tb-init` in solution 4
- Optional tracing profiles in solutions 1 and 2 include `tempo` + `otel-collector`; solution 3 includes `tempo` only

Solution 3 is implemented without the optional analytics profile. With ClickHouse enabled, Sol 3 would be ~17 containers.
Solution 4 has 8 long-lived containers plus the one-shot `tb-init` — close to Sol 0 in operational footprint, but with TigerBeetle for billing and Restate for durable execution.

## Full comparison

| Concern               | 0 — Baseline                          | 1 — Redis-Native                                                                 | 2 — Service-Grade              | 3 — Financial Core                            | 4 — TB + Restate                              |
| --------------------- | ------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------ | --------------------------------------------- | ---------------------------------------------- |
| **Auth**              | API key + Redis cache                 | JWT + OAuth                                                                      | JWT + OAuth                    | JWT + OAuth                                   | API key + Redis cache                          |
| **Queue**             | Celery + Redis                        | Redis Streams                                                                    | RabbitMQ (SLA routing)         | Redpanda + RabbitMQ dispatch                  | Restate (durable execution)                    |
| **Credit model**      | Deduct-then-execute                   | Deduct-then-execute                                                              | Reserve/capture/release        | TB pending/post/void                          | TB pending/post/void                           |
| **Credit atomicity**  | Redis Lua                             | Redis Lua (mega-script)                                                          | PG transaction                 | TigerBeetle (Jepsen-verified)                 | TigerBeetle (Jepsen-verified)                  |
| **Dual-write risk**   | Yes (mitigated: retry + reaper)       | Reduced (Lua atomic; retry on compensation)                                      | Solved (outbox)                | Solved (outbox)                               | Solved (Restate journal)                       |
| **Cancel**            | Revoke + refund                       | Redis status + refund                                                            | Release reservation            | Void pending transfer                         | Void pending transfer                          |
| **Concurrency limit** | Redis INCR/DECR                       | Lua-enforced                                                                     | Reservation count              | Redis INCR/DECR                               | TB account flag                                |
| **Task IDs**          | UUIDv7 (app-generated)                | UUIDv7 (app-generated)                                                           | UUIDv7 (app-generated)         | UUIDv7 (app-generated, maps to TB u128)       | UUIDv7 (maps to TB u128)                       |
| **Idempotency**       | Redis key                             | Lua-checked                                                                      | PG unique constraint           | TB transfer ID = task ID                      | PG unique constraint + Restate                 |
| **Webhook**           | No                                    | Optional (async POST)                                                            | RabbitMQ webhook exchange      | Redpanda webhook consumer                     | No                                             |
| **Batch**             | No                                    | Lua in loop                                                                      | Single reservation, fan-out    | RFC (documented, not shipped)                 | No                                             |
| **Queue position**    | Approximate (LLEN)                    | Approximate (XLEN; XINFO GROUPS)                                                | RabbitMQ management API        | Redpanda consumer lag                         | N/A (Restate)                                  |
| **Replay/rebuild**    | No                                    | No                                                                               | No (consumed = gone)           | Yes (offset reset)                            | Yes (Restate journal replay)                   |
| **Worker routing**    | Round-robin (Celery)                  | Round-robin (stream)                                                             | SLA queue routing              | Hot/cold model-affinity                       | Restate (single handler)                       |
| **Failure recovery**  | Reaper refund job                     | PEL + XAUTOCLAIM + refund                                                        | Watchdog releases reservations | TB auto-timeout + reconciler                  | TB auto-timeout + Restate replay               |
| **Tiers**             | No (flat)                             | free/pro/enterprise                                                              | free/pro/enterprise            | free/pro/enterprise                           | No (flat)                                      |
| **Model classes**     | No (x+y only)                         | small/medium/large                                                               | small/medium/large             | small/medium/large                            | No (x+y only)                                  |
| **Logging**           | structlog JSON                        | structlog JSON                                                                   | structlog JSON                 | structlog JSON                                | structlog JSON                                 |
| **Metrics**           | Prometheus client                     | Prometheus client                                                                | Prometheus client              | Prometheus client                             | Prometheus client                              |
| **Tracing**           | —                                     | Optional: OTel+Tempo                                                             | RFC: OTel+Tempo                | RFC: OTel+Tempo                               | —                                              |
| **Log search**        | —                                     | —                                                                                | RFC: OpenSearch                | RFC: OpenSearch                               | —                                              |
| **OLAP**              | —                                     | —                                                                                | —                              | RFC: ClickHouse                               | —                                              |
| **Alerting**          | Prometheus rules                      | Prometheus rules                                                                 | Prometheus rules               | Prometheus rules                              | —                                              |
| **Dashboards**        | Grafana                               | Grafana                                                                          | Grafana                        | Grafana                                       | Grafana                                        |
| **Containers**        | ~7                                    | ~9                                                                               | ~12                            | ~15                                           | ~8                                             |
| **PG on hot path**    | Auth miss only                        | Submit + worker writes (poll/auth zero-PG)                                       | Command writes (txn)           | Command metadata only                         | Metadata only (billing in TB)                  |
| **Key strength**      | Simple, complete, pragmatic           | Zero-PG hot path                                                                 | Correct under all failures     | Financial-grade + replayable + model-affinity | ~1.8k LOC, Jepsen-verified, auto-replay        |

## Cross-cutting decisions

### UUIDv7 for task IDs

All solutions generate task IDs using UUIDv7 (RFC 9562) — time-ordered UUIDs where the first 48 bits encode a millisecond timestamp. Generated application-side via Python `uuid6.uuid7()`, not Postgres `gen_random_uuid()`.

Why: UUIDv4 causes random B-tree page splits on every insert. UUIDv7 appends sequentially, eliminating write amplification on primary key indexes. `ORDER BY task_id` becomes equivalent to `ORDER BY created_at` without a separate index.

## Reproducibility

Local/dev runs use hardcoded defaults (API keys, credentials, OAuth keypair).
Production must externalize secrets and support key rotation.
