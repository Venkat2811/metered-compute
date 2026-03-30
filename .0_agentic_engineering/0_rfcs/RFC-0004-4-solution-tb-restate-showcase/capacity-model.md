# RFC-0004: Capacity Model

Parent: [RFC-0004 README](./README.md)

Target: **50,000 customers, 30M task submissions/day**

> This capacity model uses the same workload profile as Sol 0/2 (50K users, 30M submits/day, 5 polls per task). The key difference: TigerBeetle handles all billing load (no credit SQL in Postgres), and Restate handles all workflow orchestration (no outbox relay). Postgres carries only metadata.

---

## Workload profile

| Parameter              | Value              |
| ---------------------- | ------------------ |
| Total customers        | 50,000             |
| Daily task submissions | 30,000,000         |
| Avg polls per task     | 5                  |
| Cancel rate            | 3%                 |
| Task runtime (average) | 2.5s (demo: 0.5s)  |
| Task cost              | 10 credits         |

### Daily request volumes

| Request type     | Daily volume | Avg req/s | Peak req/s (~3x avg) |
| ---------------- | ------------ | --------- | -------------------- |
| Submit           | 30,000,000   | 347       | ~1,040               |
| Poll             | 150,000,000  | 1,736     | ~5,200               |
| Cancel           | 900,000      | 10        | ~30                  |
| Admin credits    | ~10,000      | <1        | ~3                   |
| **Total**        | **~181M**    | **~2,094**| **~6,270**           |

---

## Per-component capacity

### Postgres (metadata only)

Unlike Sol 0/2, Postgres does NOT handle billing. It stores users, API keys, and task metadata.

| Operation                  | Calls/task lifecycle | Peak calls/sec | Notes                                    |
| -------------------------- | -------------------- | -------------- | ---------------------------------------- |
| Auth (cache miss, ~10%)    | 0.1                  | ~104           | 90% served from Redis cache              |
| INSERT tasks               | 1                    | ~1,040         | Submit path                              |
| UPDATE tasks (RUNNING)     | 1                    | ~1,040         | Restate workflow step 1                  |
| UPDATE tasks (COMPLETED)   | 1                    | ~1,040         | Restate workflow step 4                  |
| SELECT tasks (poll miss)   | 0.2                  | ~208           | ~80% served from Redis cache             |
| SELECT tasks (cancel)      | 0.03                 | ~31            | 3% cancel rate                           |
| UPDATE tasks (CANCELLED)   | 0.03                 | ~31            | 3% cancel rate                           |
| UPDATE users (admin)       | negligible           | <3             | Mirror TB balance                        |
| **Total PG writes (peak)** |                      | **~3,180**     | INSERT + 2 UPDATEs + cancel              |
| **Total PG reads (peak)**  |                      | **~343**       | Auth miss + poll miss + cancel SELECT    |

**Comparison to Sol 2**: Sol 2 PG carries ~5,600 peak writes/sec (including credit reservations, outbox events, inbox events, credit transactions, task commands + query projections). Sol 4 cuts PG writes by ~43% because all billing SQL is in TigerBeetle.

**Postgres storage (1 year)**:

| Table      | Row size (avg) | Rows/day   | Annual storage |
| ---------- | -------------- | ---------- | -------------- |
| tasks      | ~400 bytes     | 30,000,000 | ~4.4 TB        |
| users      | ~200 bytes     | negligible | <10 MB         |
| api_keys   | ~100 bytes     | negligible | <1 MB          |
| **Total**  |                |            | **~4.4 TB**    |

Compare Sol 2: ~8.7 TB (tasks + credit_reservations + credit_transactions + outbox + inbox). Sol 4 cuts PG storage by ~50%.

### TigerBeetle (billing engine)

| Operation              | Calls/task lifecycle | Peak calls/sec | Notes                              |
| ---------------------- | -------------------- | -------------- | ---------------------------------- |
| pending_transfer       | 1                    | ~1,040         | Submit: user → escrow              |
| post_pending_transfer  | 1                    | ~1,040         | Workflow: capture credits          |
| void_pending_transfer  | 0.03                 | ~31            | Cancel: return credits             |
| ensure_user_account    | ~0.01                | ~10            | First interaction only (idempotent)|
| direct_transfer        | negligible           | <3             | Admin topup                        |
| lookup_accounts        | negligible           | <3             | Admin topup (get balance)          |
| **Total TB ops (peak)**|                      | **~2,124**     |                                    |

TigerBeetle is designed for >1M transfers/sec on a single node. Peak of ~2,124 ops/sec is <0.3% of TB capacity. TB is not the bottleneck.

**TigerBeetle storage**: TB stores accounts and transfers in a fixed-size data file. At 30M transfers/day (~60M including pending + post), annual storage is ~2 TB. TB's LSM-style storage is significantly more compact than equivalent PG rows.

### Redis (cache)

| Key pattern       | Operations/sec (peak) | Memory per key | Total keys (steady state) | Memory    |
| ----------------- | --------------------- | -------------- | ------------------------- | --------- |
| `auth:{hash}`     | ~6,270 GET            | ~200 bytes     | ~50,000 (1 per user)      | ~10 MB    |
| `task:{id}`       | ~5,200 GET + ~2,080 SET| ~500 bytes    | ~2.6M (24h TTL)           | ~1.3 GB   |
| **Total**         |                       |                |                           | **~1.3 GB**|

Compare Sol 0/1: ~14 GB (credit balances, task hashes, active counters, rate limits, stream data). Sol 4 cuts Redis memory by ~90% because billing state is in TB, not Redis.

### Restate (workflow engine)

| Metric                     | Value           | Notes                                    |
| -------------------------- | --------------- | ---------------------------------------- |
| Invocations/sec (peak)     | ~1,040          | 1 workflow per task submit                |
| Journal entries/invocation | ~5              | mark_running + compute + capture + store + cache |
| Journal entries/sec (peak) | ~5,200          | 1,040 x 5 steps                          |
| Avg invocation duration    | ~1-3s           | Depends on compute time                  |

Restate is designed for high-throughput workflow orchestration. 5,200 journal entries/sec is well within single-node capacity.

### API instances

| Metric               | Value    | Notes                                         |
| -------------------- | -------- | --------------------------------------------- |
| Peak req/sec (total) | ~6,270   | Across all endpoints                          |
| req/sec per instance | ~3,000   | FastAPI async with uvicorn workers            |
| Instances needed     | 2-3      | 2 for HA, 3 for headroom                     |

Same as Sol 0/1. The API is a thin routing layer — no heavy computation, no blocking SQL.

---

## Scaling triggers

| Trigger                                  | Current headroom | Action                                           |
| ---------------------------------------- | ---------------- | ------------------------------------------------ |
| PG writes > 10K/sec                      | 3x headroom      | Read replicas for poll; partition tasks by month  |
| Redis memory > 4 GB                      | 3x headroom      | Reduce TTLs or add Redis Cluster                 |
| TB ops > 100K/sec                        | 50x headroom     | TB cluster (multi-replica)                        |
| Restate journal > 50K entries/sec        | 10x headroom     | Restate cluster                                  |
| API instances > 5                        | 2x headroom      | Add load balancer tier                            |

---

## Comparison: DB call overhead per task lifecycle

| Phase               | Sol 0 (PG-only) | Sol 2 (CQRS+Outbox) | Sol 4 (TB+Restate) |
| ------------------- | ---------------- | -------------------- | ------------------- |
| Auth                | 1 PG             | 0 (JWT) + 1 Redis   | 0-1 PG + 1 Redis   |
| Submit              | 4+ PG            | 6 PG (1 txn)        | 1 PG + 1 TB        |
| Workflow/worker     | 2 PG             | 3 PG (1 txn) + 1 MQ | 2 PG + 1 TB        |
| Poll (5x)           | 5 PG             | 0-1 PG + 5 Redis    | 0-1 PG + 5 Redis   |
| **Total PG calls**  | **~12**          | **~10**              | **~4**              |
| **Total all calls** | **~12**          | **~16**              | **~14**             |

Sol 4 cuts PG calls by ~67% vs Sol 0 and ~60% vs Sol 2. The total call count is comparable to Sol 2, but the load is distributed across purpose-built systems (TB for billing, Redis for cache, Restate for orchestration) rather than concentrated on Postgres.
