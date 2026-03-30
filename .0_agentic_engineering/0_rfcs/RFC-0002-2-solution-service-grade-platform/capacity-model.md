# RFC-0002: Capacity Model

Parent: [RFC-0002 README](./README.md)

Target: **50,000 customers, 30M task submissions/day**

> **Compute model note:** This model assumes each task is CPU-bound, occupying 1 compute unit for the full task runtime. The assignment code simulates inference with `time.sleep()`, but the capacity model uses the production-realistic assumption: **1 task = 1 compute unit**. This maps directly to GPU-bound inference — replace "CPU core" with "GPU" and the numbers hold.

> **Key difference vs solutions 0/1:** RabbitMQ replaces Celery/Redis Streams as the task queue. Redis is a query cache only (much smaller footprint). Postgres carries more tables (task_commands, credit_reservations, outbox_events, inbox_events, task_query_view) and is on the command path for transactional writes. Queue memory lives in RabbitMQ, not Redis. `T_avg` uses 2.5s (small model default, same as Sol 0) because the compose-scale analytical model assumes the small model class for baseline sizing.

---

## Workload profile

| Parameter              | Value              |
| ---------------------- | ------------------ |
| Total customers        | 50,000             |
| Daily task submissions | 30,000,000         |
| Avg polls per task     | 5                  |
| Cancel rate            | 3%                 |
| Worker failure rate    | ~1% (assumption)   |
| Task runtime (average) | 2.5s (small model) |

### User activity segments

| Segment           | Share | Users  | Submit share | Submits/user/day |
| ----------------- | ----- | ------ | ------------ | ---------------- |
| Extremely active  | 10%   | 5,000  | ~40%         | 2,400            |
| Very active       | 20%   | 10,000 | ~30%         | 900              |
| Moderately active | 20%   | 10,000 | ~20%         | 600              |
| Occasional        | 50%   | 25,000 | ~10%         | 120              |

### Daily request volumes

| Request type     | Daily volume | Avg req/s  | Peak req/s (~3x avg) |
| ---------------- | ------------ | ---------- | -------------------- |
| Submit           | 30,000,000   | 347        | ~1,040               |
| Poll             | 150,000,000  | 1,736      | ~5,200               |
| Cancel           | 900,000      | 10         | ~30                  |
| Auth (every req) | 180,900,000  | 2,094      | ~6,270               |
| **Total**        | **~181M**    | **~2,094** | **~6,270**           |

Auth line: JWT verification is local crypto (no network), but revocation check adds 1 Redis RTT per request. OAuth token acquisition (~100K-150K/day) is negligible.

---

## Hourly traffic shape

Three peak windows: morning start (8-10am), end of work (4-6pm), before dinner (6-8pm).

```text
Hour  Submit/s  Total req/s  |  Traffic shape
----  --------  -----------  |
 00       67          400    |  ##
 01       42          250    |  #
 02       25          150    |  #
 03       25          150    |  #
 04       42          250    |  #
 05       83          500    |  ##
 06      167        1,000    |  ####
 07      333        2,000    |  ########
 08      667        4,000    |  ################
 09      833        5,000    |  ####################  <- morning peak
 10      583        3,500    |  ##############
 11      458        2,750    |  ###########
 12      333        2,000    |  ########
 13      333        2,000    |  ########
 14      417        2,500    |  ##########
 15      458        2,750    |  ###########
 16      750        4,500    |  ################
 17      750        4,500    |  ################      <- end-of-work peak
 18      583        3,500    |  ##############
 19      583        3,500    |  ##############        <- before-dinner peak
 20      333        2,000    |  ########
 21      250        1,500    |  ######
 22      133          800    |  ###
 23       83          500    |  ##
```

Total req/s = submit/s x 6 (1 submit + 5 polls per task lifecycle).

---

## Postgres storage

### Per-table growth

| Table                 | Rows/day | Row size | Daily growth | Retention               | Steady-state |
| --------------------- | -------- | -------- | ------------ | ----------------------- | ------------ |
| `task_commands`       | 30M      | ~400 B   | 12.0 GB      | 90 days                 | 1.08 TB      |
| `credit_reservations` | 30M      | ~200 B   | 6.0 GB       | 180 days                | 1.08 TB      |
| `credit_transactions` | 31.2M    | ~150 B   | 4.7 GB       | 365 days                | 1.7 TB       |
| `outbox_events`       | 30M+     | ~300 B   | 9.0 GB       | 24h (purge after relay) | 9 GB         |
| `inbox_events`        | ~30M     | ~100 B   | 3.0 GB       | 7 days (dedup window)   | 21 GB        |
| `task_query_view`     | 30M      | ~350 B   | 10.5 GB      | 120 days                | 1.26 TB      |
| `users`               | 50K      | ~200 B   | ~0           | permanent               | 10 MB        |
| `api_keys`            | 50K      | ~120 B   | ~0           | permanent               | 6 MB         |

`task_commands` rows are ~400 B (larger than Sol 0 `tasks` at ~350 B) because of additional columns: `tier`, `mode`, `model_class`, `callback_url`, `idempotency_key`.

`credit_transactions` rows = 30M deductions + 900K cancel refunds + 300K failure refunds.

`outbox_events` are ephemeral: the relay publishes events to RabbitMQ, then marks them `published_at`. A purge job deletes published rows older than 24h. Effective steady-state is ~1 day's worth of events.

`inbox_events` stores only `(event_id, consumer_name, processed_at)` for dedup. 7-day retention covers any replay window. Rows are minimal (~100 B).

`api_keys` uses SHA-256 hashed keys (`CHAR(64)` primary key) -- no plaintext storage.

### With indexes

`task_commands` has 4 indexes (PK + user_idem unique + status partial + user_status); `credit_reservations` has 3 indexes (PK + task_id unique + state_expires); `credit_transactions` has 2 indexes (PK + user_created); `outbox_events` has 2 indexes (PK + unpublished partial); `inbox_events` has 1 index (PK); `task_query_view` has 3 indexes (PK + user_updated + status partial).

| Table                          | Data    | Index overhead | Total       |
| ------------------------------ | ------- | -------------- | ----------- |
| `task_commands` (90d)          | 1.08 TB | ~650 GB (~60%) | **~1.7 TB** |
| `credit_reservations` (180d)   | 1.08 TB | ~540 GB (~50%) | **~1.6 TB** |
| `credit_transactions` (365d)   | 1.7 TB  | ~850 GB (~50%) | **~2.5 TB** |
| `task_query_view` (120d)       | 1.26 TB | ~630 GB (~50%) | **~1.9 TB** |
| `outbox_events` (24h)          | 9 GB    | ~5 GB (~55%)   | **~14 GB**  |
| `inbox_events` (7d)            | 21 GB   | ~10 GB (~50%)  | **~31 GB**  |
| Other tables (`users`, `keys`) | <20 MB  | <20 MB         | **<40 MB**  |
| **Total Postgres**             |         |                | **~7.7 TB** |

UUIDv7 primary keys keep B-tree inserts sequential (append-only). No random page splits. `ORDER BY task_id` = `ORDER BY created_at` implicitly.

Note: Sol 2 has nearly double the PG disk of Sol 0 (~4 TB) due to the additional tables: `credit_reservations` (1.6 TB), `task_query_view` (1.9 TB), and the CQRS infrastructure tables. This is the tradeoff for transactional correctness and CQRS separation.

### Daily storage growth (data + indexes)

| Store              | Component                         | Daily growth | Monthly growth |
| ------------------ | --------------------------------- | ------------ | -------------- |
| Postgres           | `task_commands` (data + indexes)  | 19.2 GB      | 576 GB         |
| Postgres           | `credit_reservations` (d+i)       | 9.0 GB       | 270 GB         |
| Postgres           | `credit_transactions` (d+i)       | 7.1 GB       | 213 GB         |
| Postgres           | `task_query_view` (d+i)           | 15.8 GB      | 474 GB         |
| Postgres           | `outbox_events` (d+i, 24h window) | net 0        | net 0          |
| Postgres           | `inbox_events` (d+i, 7d window)   | net 0        | net 0          |
| Postgres           | WAL / bloat overhead (~10%)       | 5.1 GB       | 153 GB         |
| **Postgres total** |                                   | **56.2 GB**  | **1,686 GB**   |
| Redis              | `task:{task_id}` (24h TTL)        | net 0        | net 0          |
| **Redis total**    |                                   | **net 0**    | **net 0**      |
| RabbitMQ           | Queue messages (consumed=gone)    | net 0        | net 0          |
| **RabbitMQ total** |                                   | **net 0**    | **net 0**      |

Postgres grows ~56 GB/day (vs ~26 GB/day in Sol 0). Redis is stable at ~6-7 GB (TTL-bounded). RabbitMQ is flow-through (consumed messages are gone).

`outbox_events` and `inbox_events` show net 0 growth because they are bounded by short retention windows (24h and 7d respectively). After their retention windows fill, purges match inserts.

### Cumulative Postgres disk (data + indexes)

```text
Month   task_cmds   reserv    credit_txn  query_view  Total      |  Disk usage
-----   ---------   ------    ----------  ----------  ------     |
  1      576 GB     270 GB     213 GB      474 GB     1,533 GB   |  ####
  2    1,152 GB     540 GB     426 GB      948 GB     3,066 GB   |  ########
  3    1,728 GB     810 GB     639 GB    1,422 GB     4,599 GB   |  ############  <- task_cmds 90d
  4    1,728 GB   1,080 GB     852 GB    1,896 GB     5,556 GB   |  ##############  <- query_view 120d
  5    1,728 GB   1,080 GB   1,065 GB    1,896 GB     5,769 GB   |  ###############
  6    1,728 GB   1,080 GB   1,278 GB    1,896 GB     5,982 GB   |  ################  <- reserv 180d
  7    1,728 GB   1,080 GB   1,491 GB    1,896 GB     6,195 GB   |  ################
  9    1,728 GB   1,080 GB   1,917 GB    1,896 GB     6,621 GB   |  #################
 12    1,728 GB   1,080 GB   2,556 GB    1,896 GB     7,260 GB   |  ###################
 13    1,728 GB   1,080 GB   2,556 GB    1,896 GB     7,260 GB   |  ###################  <- steady-state
```

- `task_commands` reaches steady-state at month 3 (90d retention window fills)
- `task_query_view` reaches steady-state at month 4 (120d retention window fills)
- `credit_reservations` reaches steady-state at month 6 (180d retention window fills)
- `credit_transactions` reaches steady-state at month 12 (365d retention window fills)
- After month 12, disk is flat at **~7.3 TB** -- purges match inserts
- `outbox_events` (24h) and `inbox_events` (7d) reach steady-state in week 1 and are negligible (~45 GB combined)

Note: numbers exclude WAL overhead and VACUUM bloat (add ~10% headroom). Provision 8 TB to account for transient bloat during bulk deletes. Sol 2 requires roughly **1.8x the disk of Sol 0** (~7.3 TB vs ~4 TB).

---

## Redis memory

### Working set

| Key pattern          | Count (24h window) | Size/key | Total      | TTL  |
| -------------------- | ------------------ | -------- | ---------- | ---- |
| `task:{task_id}`     | 30M                | ~200 B   | **6.0 GB** | 24h  |
| Rate limit counters  | ~50K               | ~80 B    | 4 MB       | 60s  |
| Concurrency counters | ~10K in-flight     | ~70 B    | 700 KB     | none |
| `revoked:{uid}:{d}`  | ~10K entries/day   | ~70 B    | <1 MB      | 36h  |
| `auth:{api_key}`     | ~15K concurrent    | ~200 B   | 3 MB       | 60s  |
| **Total**            |                    |          | **~6 GB**  |      |

vs Sol 0 (~11.1 GB) and Sol 1 (~14.2 GB): Redis memory drops by ~45-58% because:

- **No `result:{task_id}` hashes** -- the `task:{task_id}` hash stores status + result in one key (~200 B combined)
- **No `idem:{user_id}:{key}` keys** -- idempotency is enforced by a Postgres unique constraint on `(user_id, idempotency_key)`, not Redis
- **No `credits:{user_id}` keys** -- credit balances live in the `users` table (PG is the authority)
- **No `credits:dirty` set** -- no credit drift reaper needed (reservation model is self-correcting)
- **No Celery broker queue / Redis Streams** -- task queue is in RabbitMQ
- **No `pending:{task_id}` markers** -- RabbitMQ handles in-flight tracking via ack/nack

A single **8 GB Redis instance** covers this with ample headroom for AOF rewrite buffers. This is half the memory of Sol 0 (16 GB) and a quarter of Sol 1 fixed-model (32 GB).

---

## RabbitMQ memory

### Queue topology

| Queue            | Purpose                        | Consumer            | Message TTL |
| ---------------- | ------------------------------ | ------------------- | ----------- |
| `queue.realtime` | Enterprise tier, sync mode     | Workers (priority)  | 30s         |
| `queue.fast`     | Pro tier, async mode           | Workers             | 5 min       |
| `queue.batch`    | Free tier, batch mode          | Workers             | 30 min      |
| `queue.dlq`      | Failed messages after retries  | Ops / manual review | 7 days      |
| `exchange.tasks` | Topic exchange, routes by tier | N/A (exchange)      | N/A         |
| `queue.webhooks` | Webhook delivery callbacks     | Webhook worker      | 1 hour      |

### Queue depth and memory

At **fixed capacity** (1,200 compute units, drain = 480/sec), queue depth builds during peaks (same pattern as Sol 0):

| Metric                   | Value                         |
| ------------------------ | ----------------------------- |
| Peak queue depth         | ~3.55M messages (at 7pm)      |
| Message size (avg)       | ~500 B (event payload + hdrs) |
| Peak queue memory        | ~1.78 GB                      |
| DLQ at steady-state      | near-empty (< 1 MB)           |
| Webhook queue steady     | < 10 MB                       |
| RabbitMQ overhead        | ~2 GB (mgmt, flow control)    |
| **RabbitMQ recommended** | **4 GB**                      |

At **elastic capacity** (200-2,500 units), peak queue depth is ~32K messages (~16 MB). RabbitMQ runs comfortably on 2 GB.

RabbitMQ is designed for flow-through, not storage. Unlike Redis Streams, consumed messages are gone. The DLQ is the only retention-bearing queue, and it should be near-empty in normal operation. If the DLQ grows beyond a few thousand entries, it indicates a systemic problem requiring ops intervention.

### Memory breakdown at peak (fixed model)

| Component                 | Memory    |
| ------------------------- | --------- |
| Queue messages (3.55M)    | 1.78 GB   |
| Message indices           | ~200 MB   |
| Management plugin         | ~200 MB   |
| Erlang VM overhead        | ~500 MB   |
| Flow control buffers      | ~300 MB   |
| **Total RabbitMQ memory** | **~3 GB** |

Provision a 4 GB RabbitMQ instance for the fixed model. The elastic model runs on 2 GB.

---

## Network transfer

### Per-request breakdown

| Request type      | API-Redis                     | API-Postgres                          | API-RabbitMQ | Total/req   |
| ----------------- | ----------------------------- | ------------------------------------- | ------------ | ----------- |
| Submit            | ~300 B (cache write-through)  | ~2.5 KB (txn: reserve + cmd + outbox) | 0 (outbox)   | **~2.8 KB** |
| Poll (cache hit)  | ~450 B (revocation + HGETALL) | 0                                     | 0            | **~450 B**  |
| Poll (cache miss) | ~200 B (revocation)           | ~400 B (SELECT query view)            | 0            | **~600 B**  |
| Cancel            | ~200 B (cache update)         | ~1.5 KB (txn: release + refund)       | 0            | **~1.7 KB** |
| Admin credits     | 0                             | ~600 B (UPDATE)                       | 0            | **~600 B**  |

Submit is heavier on PG than Sol 0 (~2.5 KB vs ~1 KB) because the command transaction writes to 3 tables (`task_commands`, `credit_reservations`, `outbox_events`) in one txn. But submit has zero direct RabbitMQ traffic -- the outbox relay handles publish asynchronously.

### Internal flows (not client-facing)

| Flow                       | Calculation                  | Per-event  | Daily transfer |
| -------------------------- | ---------------------------- | ---------- | -------------- |
| Outbox relay -> RabbitMQ   | 30M events x 500 B           | ~500 B     | 15 GB          |
| Outbox relay -> PG         | 30M UPDATEs (mark published) | ~100 B     | 3 GB           |
| RabbitMQ -> Workers        | 30M messages x 500 B         | ~500 B     | 15 GB          |
| Workers -> PG (completion) | 30M txns x 1.5 KB            | ~1.5 KB    | 45 GB          |
| Workers -> Redis (cache)   | 30M cache writes x 200 B     | ~200 B     | 6 GB           |
| Projector -> PG            | 30M upserts x 400 B          | ~400 B     | 12 GB          |
| Watchdog -> PG             | marginal (expired scan)      | negligible | < 1 GB         |

### Daily aggregate

| Flow                  | Calculation                 | Daily transfer                               |
| --------------------- | --------------------------- | -------------------------------------------- |
| Client-facing submits | 30M x 2.8 KB               | 84 GB                                        |
| Client-facing polls   | 150M x 450 B               | 67.5 GB                                      |
| Client-facing cancels | 900K x 1.7 KB              | 1.5 GB                                       |
| Internal flows        | relay + workers + projector | 97 GB                                        |
| **Total**             | **153 + 97 GB**             | **~250 GB/day (avg 23 Mbps, peak ~70 Mbps)** |

All internal Docker network traffic. No egress costs in Compose. Daily transfer is ~1.7x Sol 0 (~144 GB) because the outbox relay, RabbitMQ delivery, and projector add internal hops that do not exist in Sol 0's direct-enqueue model.

---

## Worker sizing

### Compute model

Each task occupies **1 compute unit** (CPU core) for the full task runtime. No oversubscription.

- **Assignment simulation:** `time.sleep(2.5s)` -- the core is idle but reserved
- **Production inference:** 1 GPU per task -- the GPU is fully utilized
- The numbers below apply to both. Replace "core" with "GPU" for production.

Workers consume from RabbitMQ via `basic_consume` across 3 tiered queues (realtime, fast, batch). Each worker handles 1 concurrent task. RabbitMQ prefetch count = 1 (no message buffering).

### Compute demand throughout the day

Compute units needed = `submit_rate x task_runtime`. Each worker process handles 1 concurrent task (C=1).

```text
Hour  Submit/s  Compute units  |  Worker demand
----  --------  -------------  |
 00       67          168      |  ##
 01       42          105      |  #
 02       25           63      |  #
 03       25           63      |  #
 04       42          105      |  #
 05       83          208      |  ###
 06      167          418      |  #####
 07      333          833      |  ##########
 08      667        1,668      |  ##################
 09      833        2,083      |  #######################  <- peak
 10      583        1,458      |  ###############
 11      458        1,145      |  ##############
 12      333          833      |  ##########
 13      333          833      |  ##########
 14      417        1,043      |  ############
 15      458        1,145      |  ##############
 16      750        1,875      |  ##################
 17      750        1,875      |  ##################        <- peak
 18      583        1,458      |  ###############
 19      583        1,458      |  ###############           <- peak
 20      333          833      |  ##########
 21      250          625      |  ########
 22      133          333      |  ####
 23       83          208      |  ###
```

### Resource profile per compute unit

| Resource             | Per unit              | Peak (2,083 units) | Trough (63 units) |
| -------------------- | --------------------- | ------------------ | ----------------- |
| CPU / GPU            | 1 core (or 1 GPU)    | 2,083              | 63                |
| Memory               | ~100 MB (Python + IO) | ~208 GB            | ~6.3 GB           |
| PG connections       | 1                     | 2,083              | 63                |
| RabbitMQ connections | 1                     | 2,083              | 63                |

At peak, PG connections from workers alone exceed default `max_connections=100`. Production requires PgBouncer or connection pooling middleware in front of Postgres.

Note: workers connect to RabbitMQ (not Redis) for task consumption. Each worker holds 1 RabbitMQ connection with 1 channel. RabbitMQ handles 2,083 concurrent connections comfortably (Erlang is designed for this). Workers still connect to Redis for cache write-through on task completion.

### Scaling strategy

**Fixed capacity (Compose):**
Provision 1,200 compute units. Accept queue buildup during peaks.

- Drain rate: 1,200 / 2.5 = 480 tasks/sec
- Handles all hours where submit <= 480/sec
- During peaks (08-10, 16-19), RabbitMQ queues absorb overflow

**Elastic capacity (production):**
Scale compute units based on RabbitMQ queue depth signal.

| Trigger                     | Action              | Target        |
| --------------------------- | ------------------- | ------------- |
| Queue depth > 100 for 30s   | Scale up workers    | +20% capacity |
| Queue depth > 1,000 for 60s | Scale up workers    | +50% capacity |
| Queue depth = 0 for 5 min   | Scale down workers  | -25% capacity |
| Queue depth = 0 for 15 min  | Scale down to floor | Minimum fleet |

- Minimum fleet: 200 units (drain 80/sec -- covers overnight)
- Maximum fleet: 2,500 units (covers peak 2,083 + 20% headroom)

Queue depth signal comes from RabbitMQ management API (`/api/queues` endpoint) or Prometheus metrics via the `rabbitmq_prometheus` plugin. This replaces the Redis `XLEN` / Celery inspect signals used in Sol 0/1.

---

## API server sizing

FastAPI with uvicorn is async -- a single process handles thousands of concurrent connections. JWT verification is local crypto (~0.1ms/req). The bottleneck is connection pool fan-out, not CPU.

### Per-instance capacity

| Resource                         | Value                          |
| -------------------------------- | ------------------------------ |
| Concurrent requests per instance | ~2,000-3,000 (async I/O bound) |
| CPU per instance                 | ~0.5-1 core                    |
| Memory per instance              | ~200 MB                        |
| PG pool per instance             | max 10 connections             |
| Redis pool per instance          | ~10 connections                |

### Scaling through the day

```text
Hour  Total req/s  API instances  PG connections  Redis connections
----  -----------  -------------  --------------  ----------------
 00          400        2 (HA)            20              20
 03          150        2 (HA)            20              20
 06        1,000        2                 20              20
 08        4,000        3                 30              30
 09        5,000        3                 30              30        <- peak
 12        2,000        2                 20              20
 16        4,500        3                 30              30        <- peak
 19        3,500        2                 20              20
 22          800        2 (HA)            20              20
```

Minimum 2 instances always (HA). Scale to 3 during peak windows. Each instance handles ~2,000 req/s of async I/O. Total PG connection ceiling from API: 30 connections (well within Postgres limits even with worker connections pooled via PgBouncer).

Note: API PG load is heavier in Sol 2 than Sol 0 because submits write to 3 tables per transaction (task_commands + credit_reservations + outbox_events). Each submit is ~3 INSERT statements vs Sol 0's 1 INSERT. The connection pool handles this because the transaction is fast (~2-5ms total), but monitor PG CPU and WAL write throughput.

**Scaling signal:** HTTP request latency p99. Scale up when p99 > 200ms sustained for 60s.

---

## Queue depth

Queue depth depends on the gap between submit rate and worker drain capacity. In Sol 2, the queue lives in RabbitMQ (not Redis).

### Fixed-capacity model (1,200 compute units, drain = 480 tasks/sec)

```text
Hour  Submit/s  Drain/s  Net/s   Queue depth  |  Queue (each block ~ 100K msgs)
----  --------  -------  ------  -----------  |
 00       67      480    -413           0     |
 01       42      480    -438           0     |
 02       25      480    -455           0     |
 03       25      480    -455           0     |
 04       42      480    -438           0     |
 05       83      480    -397           0     |
 06      167      480    -313           0     |
 07      333      480    -147           0     |
 08      667      480    +187       673K     |  #######
 09      833      480    +353      1.94M     |  ################
 10      583      480    +103      2.31M     |  ###################
 11      458      480     -22      2.24M     |  ##################
 12      333      480    -147      1.71M     |  ###############
 13      333      480    -147      1.18M     |  ############
 14      417      480     -63       950K     |  ##########
 15      458      480     -22       871K     |  #########
 16      750      480    +270      1.84M     |  ################
 17      750      480    +270      2.82M     |  ########################
 18      583      480    +103      3.19M     |  ############################
 19      583      480    +103      3.55M     |  ################################ <- peak
 20      333      480    -147      3.03M     |  ##########################
 21      250      480    -230      2.20M     |  ##################
 22      133      480    -347       950K     |  ##########
 23       83      480    -397         0      |  <- cleared ~11:40pm
```

The queue shows two build/drain cycles per day:

1. **Morning peak** (08-09): builds to ~2.3M, partially drains during lunch
2. **Evening peak** (16-19): builds on top of residual, peaks at **3.55M** at 7pm
3. **Overnight drain** (20-23): clears completely by ~11:40pm

Note: these numbers are identical to Sol 0 because the worker fleet size (1,200), drain rate (480/sec), and T_avg (2.5s) are the same. The difference is WHERE the queue lives: RabbitMQ memory instead of Redis memory. RabbitMQ's flow control mechanism will apply backpressure to publishers (the outbox relay) if queue memory approaches the configured high watermark. This is a safety valve that Sol 0's Redis queue does not have.

### Elastic-capacity model (200-2,500 compute units, autoscaler)

Fleet targets `submit_rate x 2.5 x 1.2` (demand + 20% headroom). Minimum fleet: 200 units. Scaling lag: ~2 minutes per step. Scale-down hysteresis: 5-15 minutes.

During each hour transition, the fleet scales from its current size to the new target over ~2 minutes. Messages that arrive during the ramp-up form a transient queue that drains once the fleet catches up.

```text
Hour  Submit/s  Fleet   Drain/s  Queue peak  |  Fleet             Queue
----  --------  ------  -------  ----------  |  (block ~ 100u)    (block ~ 2K msgs)
 00       67     200       80          0     |  ##
 01       42     200       80          0     |  ##
 02       25     200       80          0     |  ##
 03       25     200       80          0     |  ##
 04       42     200       80          0     |  ##
 05       83     250      100       ~300     |  ###
 06      167     500      200       ~8K     |  #####              ....
 07      333   1,000      400      ~16K     |  ##########         ........
 08      667   2,000      800      ~32K     |  ################....  <- fleet + queue
 09      833   2,500    1,000       ~4K     |  ####################  ..  <- peak fleet
 10      583   2,500    1,000          0     |  ####################     (oversized)
 11      458   1,750      700          0     |  ################          (scaling down)
 12      333   1,000      400          0     |  ##########
 13      333   1,000      400          0     |  ##########
 14      417   1,250      500       ~2K     |  ############# .
 15      458   1,375      550       ~1K     |  ##############
 16      750   2,250      900      ~24K     |  ################### ....
 17      750   2,250      900          0     |  ###################     (fleet steady)
 18      583   2,250      900          0     |  ###################     (scaling down)
 19      583   1,750      700          0     |  ################
 20      333   1,000      400          0     |  ##########
 21      250     750      300          0     |  ########
 22      133     400      160          0     |  ####
 23       83     250      100          0     |  ###
```

Transient queue math (worst transitions):

- **07->08**: fleet 1,000 (drain=400), submit jumps to 667. Overflow = 267/sec x 120s = **~32K**. Drains in ~4 min once fleet reaches 2,000.
- **15->16**: fleet 1,375 (drain=550), submit jumps to 750. Overflow = 200/sec x 120s = **~24K**. Drains in ~3 min once fleet reaches 2,250.
- **08->09**: fleet already 2,000 (drain=800), submit rises to 833. Overflow = 33/sec x 120s = **~4K**. Drains in seconds.

### Fixed vs elastic comparison

| Metric                  | Fixed (1,200 units) | Elastic (200-2,500)   |
| ----------------------- | ------------------- | --------------------- |
| Peak queue depth        | 3.55M messages      | ~32K messages         |
| Worst-case wait         | 2.1 hours           | ~4 minutes            |
| Queue clears by         | ~11:40pm            | within 4-6 min        |
| Peak RabbitMQ for queue | 1.78 GB             | ~16 MB                |
| Avg fleet utilization   | 72%                 | 77%                   |
| Min compute (night)     | 1,200 units 24/7    | 200 units             |
| Peak compute            | 1,200 units 24/7    | 2,500 units (hour 09) |

Utilization math:

- Total task-hours/day: 30M tasks x 2.5s / 3,600 = 20,833 unit-hours
- Fixed: 1,200 units x 24h = 28,800 unit-hours. Utilization = 72%
- Elastic: sum of hourly fleet = 27,025 unit-hours. Utilization = 77%
- Real win: overnight fleet drops from 1,200 to 200 (83% compute savings during low-traffic hours)

### Queue memory impact

| Model   | Peak queue | Queue store | Queue memory | Redis total | RabbitMQ total |
| ------- | ---------- | ----------- | ------------ | ----------- | -------------- |
| Fixed   | 3.55M msgs | RabbitMQ    | 1.78 GB      | 6 GB        | ~3 GB          |
| Elastic | ~32K msgs  | RabbitMQ    | ~16 MB       | 6 GB        | ~1 GB          |

Key difference from Sol 0: queue memory is in RabbitMQ, not Redis. Redis stays at a constant ~6 GB regardless of queue buildup. In Sol 0, Redis grows from 11.1 GB to 12.9 GB at peak due to Celery broker queue. In Sol 2, Redis and RabbitMQ are independently sized.

### Queue latency at peak

**Fixed model:** Worst-case queue wait = 3.55M / 480 = **2.1 hours**. Tasks submitted at 7pm clear around 9pm.

**Elastic model:** Worst-case transient wait = 32K / (800-667) = 240s = **~4 minutes**. Transient queue from 07->08 transition drains by 08:06.

**Tiered queue behavior:** With 3 tiered queues, the fixed-model wait is not uniform across tiers. Workers consume from `queue.realtime` first (strict priority), then `queue.fast`, then `queue.batch`. During peak buildup:
- Enterprise (realtime): near-zero wait (workers drain this queue first)
- Pro (fast): moderate wait (drains after realtime)
- Free (batch): longest wait (drains last, absorbs most of the 3.55M backlog)

This SLA differentiation is a structural advantage over Sol 0/1 where all tasks share one queue.

---

## Infrastructure summary

### Compose deployment (fixed capacity, single host)

| Component      | Instances | CPU / GPU        | Memory      | Disk       |
| -------------- | --------- | ---------------- | ----------- | ---------- |
| API            | 2         | 2 cores          | 400 MB      | -          |
| OAuth (Hydra)  | 1         | 0.5 cores        | 256 MB      | -          |
| Workers (C=1)  | 1,200     | 1,200 cores      | 120 GB      | -          |
| Outbox relay   | 1         | 0.5 cores        | 128 MB      | -          |
| Projector      | 1         | 0.5 cores        | 128 MB      | -          |
| Watchdog       | 1         | 0.25 cores       | 64 MB       | -          |
| Webhook worker | 1         | 0.25 cores       | 64 MB       | -          |
| Redis          | 1         | 1 core           | 8 GB        | 512 MB AOF |
| Postgres       | 1         | 8 cores          | 16 GB       | 8 TB SSD   |
| RabbitMQ       | 1         | 2 cores          | 4 GB        | 1 GB       |
| Prometheus     | 1         | 1 core           | 2 GB        | 50 GB      |
| Grafana        | 1         | 0.5 cores        | 512 MB      | -          |
| **Total**      | **~12**   | **~1,217 cores** | **~152 GB** | **~8 TB**  |

Note: 1,200 workers on a single Compose host is unrealistic. This model shows what the workload demands; production uses orchestration (K8s, ECS) across multiple nodes.

vs Sol 0: 4 additional containers (outbox relay, projector, watchdog, webhook worker), 2x PG disk (8 TB vs 4 TB), half the Redis memory (8 GB vs 16 GB), new RabbitMQ instance (4 GB). PG gets more CPU (8 vs 4 cores) because it handles the heavier write path (3 tables per submit txn + outbox relay reads + projector writes).

### Production deployment (elastic, multi-node)

| Component      | Min                   | Max   | Scaling signal                 |
| -------------- | --------------------- | ----- | ------------------------------ |
| API            | 2                     | 5     | HTTP p99 latency > 200ms       |
| Workers        | 200                   | 2,500 | RabbitMQ queue depth           |
| Redis          | 1 primary + 1 replica | same  | Memory > 80% -> vertical scale |
| Postgres       | 1 primary + 1 replica | same  | Connection saturation, IOPS    |
| PgBouncer      | 2 (HA)                | same  | Worker connection fan-out      |
| RabbitMQ       | 1 (mirrored queues)   | 3     | Queue depth, memory > 70%      |
| OAuth (Hydra)  | 2 (HA)                | same  | Token request latency          |
| Outbox relay   | 1                     | 2     | Outbox lag > 1s                |
| Projector      | 1                     | 2     | Projection lag > 30s           |
| Watchdog       | 1                     | 1     | N/A (singleton)                |
| Webhook worker | 1                     | 3     | Webhook queue depth            |

### Cost drivers (ranked)

1. **Workers** -- 90%+ of compute cost. At peak: 2,083 cores (or GPUs). The dominant cost by far.
2. **Postgres disk** -- 8 TB SSD at steady-state. ~1.8x Sol 0. Dominated by `credit_transactions` (365d, 2.5 TB) + `task_query_view` (120d, 1.9 TB) + `task_commands` (90d, 1.7 TB) + `credit_reservations` (180d, 1.6 TB).
3. **RabbitMQ** -- 4 GB memory, 2 cores. New cost vs Sol 0/1. Modest but adds operational complexity (Erlang cluster management, queue mirroring, monitoring).
4. **Redis memory** -- 8 GB. Smallest of all 3 solutions. Stable regardless of customer growth.
5. **Network** -- ~250 GB/day internal. 1.7x Sol 0. Negligible cost.
6. **Sidecar services** -- outbox relay, projector, watchdog, webhook worker. 4 lightweight containers (~384 MB combined). Trivial compute cost but each adds a monitoring/alerting surface.
7. **API + OAuth** -- 2-5 instances. Trivial compared to workers.

---

## Appendix: Analytical throughput model

Formulas for quick estimation at compose scale.

### Throughput formulas

Let:

- `W` = number of worker processes
- `C` = concurrency per worker (tasks consumed concurrently)
- `U` = utilization factor (0..1)
- `T_avg` = average task runtime seconds
- `P` = average poll requests per task

Formulas:

- `R_task = (W x C x U) / T_avg` tasks/sec
- `R_poll = P x R_task` req/sec
- `M_task = R_task x 2,592,000` tasks/month
- `M_poll = R_poll x 2,592,000` polls/month

### Runtime assumptions

From the flat RFC: `W=6, C=4, U=0.70, T_avg=2.5s, P=1.5`

Note: `C=4` means each worker process can handle 4 concurrent tasks (e.g., via async consumption from RabbitMQ with prefetch=4). This is a compose-scale setting for demo purposes. The production capacity model above uses `C=1` (one task per compute unit) because each task occupies 1 GPU.

### Example A: compose scale (W=6, C=4, U=0.70, T=2.5s, P=1.5)

- `R_task = (6 x 4 x 0.70) / 2.5 = 6.72 task/sec`
- `R_poll = 1.5 x 6.72 = 10.08 req/sec`
- `M_task = 6.72 x 2,592,000 = 17,418,240 tasks/month`
- `M_poll = 10.08 x 2,592,000 = 26,127,360 polls/month`

### Example B: compose scale doubled (W=12, C=4, same U, T, P)

- `R_task = (12 x 4 x 0.70) / 2.5 = 13.44 task/sec`
- `R_poll = 1.5 x 13.44 = 20.16 req/sec`
- `M_task = 13.44 x 2,592,000 = 34,836,480 tasks/month`
- `M_poll = 20.16 x 2,592,000 = 52,254,720 polls/month`

### Per-task storage footprint

Postgres: ~400 B (task_commands) + ~200 B (credit_reservations) + ~150 B (credit_transactions) + ~300 B (outbox_events, ephemeral) + ~100 B (inbox_events, ephemeral) + ~350 B (task_query_view) = **~1.5 KB/task** (durable, across retention windows).

Redis: ~200 B/task active (task:{task_id} hash within 24h TTL window).

vs Sol 0: ~1.25 KB/task PG + ~1.1 KB/task Redis = ~2.35 KB total. Sol 2 is ~1.7 KB total (more PG, much less Redis). The PG footprint is larger per-task, but the overall storage cost is disk (cheap) rather than RAM (expensive).

### Sensitivity notes

Most sensitive variables:

- `T_avg` (model mix shifts quickly change throughput and queue depth)
- Worker count `W`
- Utilization `U` under real incident/retry behavior
- Retention windows (especially `credit_transactions` at 365d driving 2.5 TB)
- Outbox relay throughput (if relay falls behind, outbox table grows unbounded)

Operational recommendation:

- Monitor outbox lag as a primary health signal. If `outbox_unpublished_count` grows, the system is falling behind.
- RabbitMQ queue depth is the primary autoscaling signal -- prefer it over CPU-based scaling.
- Recompute this model using measured output from monitoring before any production commitment.
