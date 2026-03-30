# RFC-0001: Capacity Model

Parent: [RFC-0001 README](./README.md)

Target: **50,000 customers, 30M task submissions/day**

> **Compute model note:** This model assumes each task is CPU-bound, occupying 1 compute unit for the full task runtime. The assignment code simulates inference with `time.sleep()`, but the capacity model uses the production-realistic assumption: **1 task = 1 compute unit**. This maps directly to GPU-bound inference — replace "CPU core" with "GPU" and the numbers hold.

> **Key difference vs solution 0:** `T_avg` rises from 2.5s to 3.1s because this solution introduces model classes (small/medium/large) with a realistic mix. Workers process one message at a time via `XREADGROUP count=1` (C=1). Each compute unit is occupied longer, so peak compute demand is higher (2,582 vs 2,083 units).

---

## Workload profile

| Parameter              | Value                                           |
| ---------------------- | ----------------------------------------------- |
| Total customers        | 50,000                                          |
| Daily task submissions | 30,000,000                                      |
| Avg polls per task     | 5                                               |
| Cancel rate            | 3%                                              |
| Worker failure rate    | ~1% (assumption)                                |
| Model mix              | 60% small (2s), 30% medium (4s), 10% large (7s) |
| Task runtime (average) | 3.1s (weighted: 0.6 x 2 + 0.3 x 4 + 0.1 x 7)   |

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

Auth line: JWT verification is local crypto (no network), but revocation check adds 1 pipelined Redis RTT per request. OAuth token acquisition (~100K-150K/day) is negligible.

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

Total req/s = submit/s × 6 (1 submit + 5 polls per task lifecycle).

---

## Postgres storage

### Per-table growth

| Table                 | Rows/day     | Row size | Daily growth | Retention               | Steady-state |
| --------------------- | ------------ | -------- | ------------ | ----------------------- | ------------ |
| `tasks`               | 30M          | ~350 B   | 10.5 GB      | 90 days                 | 945 GB       |
| `credit_transactions` | 31.2M        | ~150 B   | 4.7 GB       | 365 days                | 1.7 TB       |
| `api_keys`            | 50K          | ~120 B   | ~0           | permanent               | 6 MB         |
| `credit_drift_audit`  | ~5K          | ~100 B   | ~0.5 MB      | 30 days                 | 15 MB        |
| `credit_snapshots`    | 50K (upsert) | ~60 B    | ~0           | 1 row/user              | 3 MB         |
| `token_revocations`   | ~10K         | ~68 B    | ~0.7 MB      | 2 days (partition drop) | 1.4 MB       |
| `stream_checkpoints`  | W (upsert)   | ~80 B    | ~0           | 1 row/worker            | <1 KB        |
| `users`               | 50K          | ~200 B   | ~0           | permanent               | 10 MB        |

`credit_transactions` rows = 30M deductions + 900K cancel refunds + 300K failure refunds.

`credit_drift_audit` rows depend on drift frequency. In healthy operation, most reaper cycles find zero drift, logging only when `drift != 0`. Estimate ~5K entries/day.

`api_keys` uses SHA-256 hashed keys (`CHAR(64)` primary key) — no plaintext storage.

`token_revocations` is day-partitioned (`PARTITION BY RANGE (revoked_at)`), managed by `pg_partman`. Stores only JTI (~36 bytes) per revocation, not full JWT tokens. ~10K revocations/day is a conservative estimate. Cleanup is `DROP TABLE` per partition — instant, zero vacuum. Two-day retention means steady-state is always ~1.4 MB.

### With indexes

`tasks` has 5 indexes (PK + 4 composite/partial); `credit_transactions` has 2 indexes (PK + 1 composite); `api_keys` has 2 indexes (PK + user_active); `credit_drift_audit` has 1 index (checked_at DESC).

| Table                        | Data   | Index overhead | Total       |
| ---------------------------- | ------ | -------------- | ----------- |
| `tasks` (90d)                | 945 GB | ~570 GB (~60%) | **~1.5 TB** |
| `credit_transactions` (365d) | 1.7 TB | ~850 GB (~50%) | **~2.5 TB** |
| Other tables                 | <50 MB | <50 MB         | **<100 MB** |
| **Total Postgres**           | -      | -              | **~4 TB**   |

UUIDv7 primary keys (application-generated via `uuid7()`) keep B-tree inserts sequential (append-only). No random page splits. `ORDER BY task_id` ≈ `ORDER BY created_at` implicitly.

### Daily storage growth (data + indexes)

| Store              | Component                   | Daily growth | Monthly growth |
| ------------------ | --------------------------- | ------------ | -------------- |
| Postgres           | `tasks` (data + indexes)    | 16.8 GB      | 504 GB         |
| Postgres           | `credit_transactions` (d+i) | 7.1 GB       | 213 GB         |
| Postgres           | WAL / bloat overhead (~10%) | 2.4 GB       | 72 GB          |
| **Postgres total** | -                           | **26.3 GB**  | **789 GB**     |
| Redis              | Rolling 24h TTL window      | net 0        | net 0          |
| **Redis total**    | -                           | **net 0**    | **net 0**      |

Postgres grows ~26 GB/day. Redis is stable at ~14 GB (TTL-bounded rolling window — new keys replace expired keys).

### Cumulative Postgres disk (data + indexes)

```text
Month   tasks      credit_txn   Total      |  Disk usage
-----   ------     ----------   ------     |
  1     504 GB       213 GB      717 GB    |  ##############
  2    1008 GB       426 GB    1,434 GB    |  #############################
  3    1512 GB       639 GB    2,151 GB    |  #######################################
  4    1512 GB       852 GB    2,364 GB    |  ########################################  <- tasks hits 90d
  5    1512 GB     1,065 GB    2,577 GB    |  ############################################
  6    1512 GB     1,278 GB    2,790 GB    |  ##############################################
  9    1512 GB     1,917 GB    3,429 GB    |  #########################################################
 12    1512 GB     2,556 GB    4,068 GB    |  ###################################################################
 13    1512 GB     2,556 GB    4,068 GB    |  ###################################################################  <- steady-state
```

- `tasks` reaches steady-state at month 3 (90d retention window fills, old rows purged)
- `credit_transactions` reaches steady-state at month 12 (365d retention window fills)
- After month 12, disk is flat at **~4 TB** — purges match inserts

Note: numbers exclude WAL overhead and VACUUM bloat (add ~10% headroom). Provision 4.5 TB to account for transient bloat during bulk deletes.

---

## Redis memory

### Working set

| Key pattern            | Count (24h window)     | Size/key     | Total        | TTL     |
| ---------------------- | ---------------------- | ------------ | ------------ | ------- |
| `task:{task_id}`       | 30M                    | ~150 B       | **4.5 GB**   | 24h     |
| `result:{task_id}`     | ~29M                   | ~200 B       | **5.8 GB**   | 24h     |
| `idem:{user_id}:{key}` | 30M                    | ~120 B       | **3.6 GB**   | 24h     |
| `tasks:stream`         | ~500K (default MAXLEN) | ~500 B       | **250 MB**   | trimmed |
| `credits:{user_id}`    | 50K                    | ~70 B        | 3.5 MB       | none    |
| `active:{user_id}`     | ~10K in-flight         | ~70 B        | 700 KB       | none    |
| `credits:dirty`        | <=50K members          | ~70 B/member | 3.5 MB       | none    |
| `revoked:{uid}:{day}`  | ~10K entries/day       | ~70 B        | <1 MB        | 36h     |
| `pending:{task_id}`    | 200-500 in-flight      | ~150 B       | 75 KB        | 120s    |
| **Total**              | -                      | -            | **~14.2 GB** | -       |

`revoked:{uid}:{day}` is hot-cache state; Postgres `token_revocations` remains the durable source.

Three 24h-TTL key pools dominate: task state hashes (4.5 GB) + result cache (5.8 GB) + idempotency (3.6 GB). Everything else is under 300 MB combined.

vs solution 0: Redis memory increases from 11.1 GB to 14.2 GB because this solution stores `task:{task_id}` hashes in Redis (the Lua mega-script writes task state atomically at submit time) — this is the tradeoff that enables zero-PG polls for PENDING tasks.

A single 16 GB Redis instance covers the elastic model. For the fixed model, queue backlog adds up to 1.7 GB at peak (see [Queue memory impact](#queue-memory-impact)), pushing total to ~15.9 GB — provision 32 GB for AOF rewrite headroom.

---

## Network transfer

### Per-request breakdown

| Request type       | API<->Redis                                 | API<->Postgres       | Total/req   |
| ------------------ | ------------------------------------------- | -------------------- | ----------- |
| Submit (happy)     | ~1.4 KB (revocation + Lua + pending marker) | ~1 KB (1 txn)        | **~2.5 KB** |
| Poll (cache hit)   | ~450 B (revocation + HGETALL)               | 0                    | **~450 B**  |
| Poll (PG fallback) | ~200 B (revocation)                         | ~400 B (SELECT)      | **~600 B**  |
| Cancel             | ~500 B (revocation + refund + DECR + HSET)  | ~1 KB (SELECT + txn) | **~1.5 KB** |
| Admin credits      | ~300 B (SET + SADD + DEL)                   | ~600 B (CTE)         | **~900 B**  |

### Daily aggregate

| Flow      | Calculation             | Daily transfer                               |
| --------- | ----------------------- | -------------------------------------------- |
| Submits   | 30M x 2.5 KB            | 75 GB                                        |
| Polls     | 150M x 450 B            | 67.5 GB                                      |
| Cancels   | 900K x 1.5 KB           | 1.35 GB                                      |
| **Total** | **75 + 67.5 + 1.35 GB** | **~144 GB/day (avg 13 Mbps, peak ~40 Mbps)** |

All internal Docker network traffic. No egress costs in Compose. Worker-to-store traffic (~50 GB/day for PG state transitions + Redis cache writes) is additional but same-network.

---

## Worker sizing

### Compute model

Each task occupies **1 compute unit** (CPU core) for the full task runtime. No oversubscription.

- **Assignment simulation:** `time.sleep(T)` — the core is idle but reserved
- **Production inference:** 1 GPU per task — the GPU is fully utilized
- The numbers below apply to both. Replace "core" with "GPU" for production.

Workers consume from Redis Streams via `XREADGROUP count=1` (one task at a time). Unlike Celery's prefetch model, there is no message buffering — each worker is a single-message-at-a-time consumer.

### Compute demand throughout the day

Compute units needed = `submit_rate × T_avg`. Each worker handles 1 concurrent task (C=1).

```text
Hour  Submit/s  Compute units  |  Worker demand
----  --------  -------------  |
 00       67          208     |  ##
 01       42          130     |  #
 02       25           78     |  #
 03       25           78     |  #
 04       42          130     |  #
 05       83          257     |  ###
 06      167          518     |  ######
 07      333        1,032     |  ############
 08      667        2,068     |  ####################
 09      833        2,582     |  ##########################  <- peak
 10      583        1,807     |  #####################
 11      458        1,420     |  ###############
 12      333        1,032     |  ############
 13      333        1,032     |  ############
 14      417        1,293     |  ###############
 15      458        1,420     |  ###############
 16      750        2,325     |  ###########################
 17      750        2,325     |  ###########################    <- peak
 18      583        1,807     |  #####################
 19      583        1,807     |  #####################         <- peak
 20      333        1,032     |  ############
 21      250          775     |  #########
 22      133          412     |  #####
 23       83          257     |  ###
```

Peak compute demand: 2,582 units at 9am (vs Sol 0: 2,083 at 9am). The 24% increase comes from the higher T_avg (3.1s vs 2.5s).

### Resource profile per compute unit

| Resource          | Per unit              | Peak (2,582 units) | Trough (78 units) |
| ----------------- | --------------------- | ------------------ | ----------------- |
| CPU / GPU         | 1 core (or 1 GPU)     | 2,582              | 78                |
| Memory            | ~100 MB (Python + IO) | ~258 GB            | ~7.8 GB           |
| PG connections    | 1                     | 2,582              | 78                |
| Redis connections | 1                     | 2,582              | 78                |

At peak, PG connections from workers alone exceed default `max_connections=100`. Production requires PgBouncer or connection pooling middleware in front of Postgres.

### Scaling strategy

**Fixed capacity (Compose):**
Provision 1,500 compute units. Accept queue buildup during peaks.

- Drain rate: 1,500 / 3.1 = 484 tasks/sec
- Handles all hours where submit ≤ 484/sec
- During peaks (08-10, 16-19), queue absorbs overflow

**Elastic capacity (production):**
Scale compute units based on consumer lag + queue depth signals.

| Trigger                      | Action              | Target        |
| ---------------------------- | ------------------- | ------------- |
| Consumer lag > 100 for 30s   | Scale up workers    | +20% capacity |
| Consumer lag > 1,000 for 60s | Scale up workers    | +50% capacity |
| Queue depth = 0 for 5 min    | Scale down workers  | -25% capacity |
| Queue depth = 0 for 15 min   | Scale down to floor | Minimum fleet |

- Minimum fleet: 200 units (drain 65/sec — covers overnight)
- Maximum fleet: 3,100 units (covers peak 2,582 + 20% headroom)

---

## API server sizing

FastAPI with uvicorn is async — a single process handles thousands of concurrent connections. JWT verification is local crypto (~0.1ms/req), so the auth path is CPU-bound but negligible. The bottleneck is connection pool fan-out, not CPU.

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

**Scaling signal:** HTTP request latency p99. Scale up when p99 > 200ms sustained for 60s.

---

## Queue depth

Queue depth depends on the gap between submit rate and worker drain capacity.

### Fixed-capacity model (1,500 compute units, drain = 484 tasks/sec)

```text
Hour  Submit/s  Drain/s  Net/s   Queue depth  |  Queue (each block ~ 100K msgs)
----  --------  -------  ------  -----------  |
 00       67      484    -417           0     |
 01       42      484    -442           0     |
 02       25      484    -459           0     |
 03       25      484    -459           0     |
 04       42      484    -442           0     |
 05       83      484    -401           0     |
 06      167      484    -317           0     |
 07      333      484    -151           0     |
 08      667      484    +183       659K     |  #######
 09      833      484    +349      1.92M     |  ##################
 10      583      484     +99      2.27M     |  #####################
 11      458      484     -26      2.18M     |  ##################
 12      333      484    -151      1.63M     |  ################
 13      333      484    -151      1.09M     |  ###########
 14      417      484     -67       849K     |  #########
 15      458      484     -26       755K     |  ########
 16      750      484    +266      1.71M     |  ###############
 17      750      484    +266      2.67M     |  #######################
 18      583      484     +99      3.03M     |  ##########################
 19      583      484     +99      3.38M     |  ############################## <- peak
 20      333      484    -151      2.84M     |  ########################
 21      250      484    -234      2.00M     |  ################
 22      133      484    -351       734K     |  #######
 23       83      484    -401         0      |  <- cleared ~11:30pm
```

The queue shows two build/drain cycles per day:

1. **Morning peak** (08-09): builds to ~2.3M, partially drains during lunch
2. **Evening peak** (16-19): builds on top of residual, peaks at **3.38M** at 7pm
3. **Overnight drain** (20-23): clears completely by ~11:30pm

vs Sol 0 (drain=480/s, peak=3.55M): the slightly higher drain rate (484 vs 480) from 300 additional units keeps peak queue ~5% lower despite the higher T_avg.

> **MAXLEN constraint:** The Lua admission script applies `XADD ... MAXLEN ~ N` on every enqueue (default `redis_tasks_stream_maxlen=500_000`). With the default, Redis would trim undelivered entries beyond ~500K — losing admitted tasks during sustained queue buildup. For the fixed-capacity model (peak 3.38M), raise MAXLEN to at least 4M or disable it (`0`). The elastic model never exceeds ~32K, so the 500K default is safe there. This is a tuning knob (`AppSettings.redis_tasks_stream_maxlen`), not a code change.

### Elastic-capacity model (200-3,100 compute units, autoscaler)

Fleet targets `submit_rate × 3.1 × 1.2` (demand + 20% headroom). Minimum fleet: 200 units. Scaling lag: ~2 minutes per step. Scale-down hysteresis: 5-15 minutes.

During each hour transition, the fleet scales from its current size to the new target over ~2 minutes. Messages that arrive during the ramp-up form a transient queue that drains once the fleet catches up.

```text
Hour  Submit/s  Fleet   Drain/s  Queue peak  |  Fleet                   Queue
----  --------  ------  -------  ----------  |  (block ~ 100u)          (block ~ 2K msgs)
 00       67     250       81          0    |  ###
 01       42     200       65          0    |  ##
 02       25     200       65          0    |  ##
 03       25     200       65          0    |  ##
 04       42     200       65          0    |  ##
 05       83     300       97       ~300    |  ###
 06      167     625      202       ~8K    |  ######                   ....
 07      333   1,250      403      ~16K    |  #############              ........
 08      667   2,500      806      ~32K    |  #########################   ................
 09      833   3,100    1,000       ~3K    |  ############################### ..  <- peak fleet
 10      583   3,100    1,000          0    |  ###############################     (oversized)
 11      458   2,150      694          0    |  ##################                (scaling down)
 12      333   1,250      403          0    |  #############
 13      333   1,250      403          0    |  #############
 14      417   1,550      500       ~2K    |  ################     .
 15      458   1,700      548       ~1K    |  ###############
 16      750   2,800      903      ~24K    |  ########################   ............
 17      750   2,800      903          0    |  ########################     (fleet steady)
 18      583   2,800      903          0    |  ########################     (oversized)
 19      583   2,150      694          0    |  ##################
 20      333   1,250      403          0    |  #############
 21      250     950      306          0    |  ##########
 22      133     500      161          0    |  #####
 23       83     300       97          0    |  ###
```

Transient queue math (worst transitions):

- **07→08**: fleet 1,250 (drain=403), submit jumps to 667. Overflow = 264/sec × 120s = **~32K**. Drains in ~4 min once fleet reaches 2,500.
- **15→16**: fleet 1,700 (drain=548), submit jumps to 750. Overflow = 202/sec × 120s = **~24K**. Drains in ~3 min once fleet reaches 2,800.
- **08→09**: fleet already 2,500 (drain=806), submit rises to 833. Overflow = 27/sec × 120s = **~3K**. Drains in seconds.

### Fixed vs elastic comparison

| Metric                | Fixed (1,500 units) | Elastic (200-3,100)   |
| --------------------- | ------------------- | --------------------- |
| Peak queue depth      | 3.38M messages      | ~32K messages         |
| Worst-case wait       | 1.9 hours           | ~4 minutes            |
| Queue clears by       | ~11:30pm            | within 4-6 min        |
| Peak Redis for queue  | 1.69 GB             | ~16 MB                |
| Avg fleet utilization | 72%                 | 77%                   |
| Min compute (night)   | 1,500 units 24/7    | 200 units             |
| Peak compute          | 1,500 units 24/7    | 3,100 units (hour 09) |

Utilization math:

- Total task-hours/day: 30M tasks × 3.1s / 3,600 = 25,833 unit-hours
- Fixed: 1,500 units × 24h = 36,000 unit-hours. Utilization = 72%
- Elastic: sum of hourly fleet = 33,375 unit-hours. Utilization = 77%
- Real win: overnight fleet drops from 1,500 to 200 (87% compute savings during low-traffic hours)

### Queue memory impact

| Model   | Peak queue | Queue memory | Redis total at peak |
| ------- | ---------- | ------------ | ------------------- |
| Fixed   | 3.38M msgs | 1.69 GB      | 15.9 GB             |
| Elastic | ~32K msgs  | ~16 MB       | 14.2 GB             |

Fixed model at peak is tight for a 16 GB instance (15.9 GB excludes AOF rewrite buffer). Provision 32 GB. Elastic fits comfortably in 16 GB.

### Queue latency at peak

**Fixed model:** Worst-case queue wait = 3.38M / 484 = **~1.9 hours**. Tasks submitted at 7pm clear around 9pm.

**Elastic model:** Worst-case transient wait = 32K / (806-667) = 230s = **~4 minutes**. Transient queue from 07→08 transition drains by 08:04.

---

## Infrastructure summary

### Compose deployment (fixed capacity, single host)

| Component       | Instances | CPU / GPU        | Memory      | Disk        |
| --------------- | --------- | ---------------- | ----------- | ----------- |
| API             | 2         | 2 cores          | 400 MB      | -           |
| Workers (`C=1`) | 1,500     | 1,500 cores      | 150 GB      | -           |
| Redis           | 1         | 2 cores          | 32 GB       | 1 GB AOF    |
| Postgres        | 1         | 4 cores          | 8 GB        | 4.5 TB SSD  |
| Hydra (OAuth)   | 1         | 0.5 cores        | 256 MB      | -           |
| Reaper          | 1         | 0.5 cores        | 128 MB      | -           |
| Prometheus      | 1         | 1 core           | 2 GB        | 50 GB       |
| Grafana         | 1         | 0.5 cores        | 512 MB      | -           |
| **Total**       | -         | **~1,511 cores** | **~193 GB** | **~4.5 TB** |

Note: 1,500 workers on a single Compose host is unrealistic. This model shows what the workload demands; production uses orchestration (K8s, ECS) across multiple nodes.

### Production deployment (elastic, multi-node)

| Component | Min                    | Max   | Scaling signal                 |
| --------- | ---------------------- | ----- | ------------------------------ |
| API       | 2                      | 5     | HTTP p99 latency > 200ms       |
| Workers   | 200                    | 3,100 | Consumer lag / queue depth     |
| Redis     | 1 primary + 2 replicas | same  | Memory > 80% -> vertical scale |
| Postgres  | 1 primary + 1 replica  | same  | Connection saturation, IOPS    |
| PgBouncer | 2 (HA)                 | same  | Worker connection fan-out      |
| Hydra     | 2 (HA)                 | same  | Token request latency          |
| Reaper    | 1                      | 1     | N/A (singleton)                |

### Cost drivers (ranked)

1. **Workers** — 90%+ of compute cost. At peak: 2,582 cores (or GPUs). The dominant cost by far.
2. **Postgres disk** — 4 TB SSD at steady-state. Dominated by `credit_transactions` (365d retention).
3. **Redis memory** — 16-32 GB. Stable regardless of customer growth (driven by 24h TTL window, not total users).
4. **Network** — ~144 GB/day internal. Negligible cost.
5. **API + Hydra** — 2-5 instances. Trivial compared to workers.

---

## Appendix: Analytical throughput model

Formulas for quick estimation at compose scale.

### Throughput formulas

Let:

- `W` = number of worker processes consuming `tasks:stream`
- `T_avg` = average task runtime seconds (model-mix weighted)
- `U` = utilization factor (0..1)
- `P` = average poll requests per task

Formulas:

- `R_task = (W × U) / T_avg` tasks/sec
- `R_poll = P × R_task` req/sec
- `M_task = R_task × 2,592,000` tasks/month
- `M_poll = R_poll × 2,592,000` polls/month

### Runtime assumptions from code

`MODEL_RUNTIME_SECONDS`:

- `small = 2s`
- `medium = 4s`
- `large = 7s`

Model mix: `small 60%`, `medium 30%`, `large 10%`.

- `T_avg = 0.6×2 + 0.3×4 + 0.1×7 = 3.1s`

### Example A: default compose runtime (W=1, U=0.70, P=3)

- `R_task = (1 × 0.70) / 3.1 = 0.226 task/sec`
- `R_poll = 3 × 0.226 = 0.678 req/sec`
- `M_task = 0.226 × 2,592,000 ≈ 585,792 tasks/month`
- `M_poll = 0.678 × 2,592,000 ≈ 1,757,376 polls/month`

### Example B: light production scale (W=4, same U, T_avg, P)

- `R_task = (4 × 0.70) / 3.1 = 0.903 task/sec`
- `R_poll = 2.709 req/sec`
- `M_task ≈ 2,341,161 tasks/month`
- `M_poll ≈ 7,023,484 polls/month`

### Per-task storage footprint

Postgres: ~1.0 KB (tasks row + indexes) + ~0.25 KB (credit_transactions) = ~1.25 KB/task.
Redis: ~1.1 KB/task active (task hash + result hash + idem, within 24h TTL window).

### Sensitivity notes

Most sensitive variables:

- `T_avg` (model mix shifts quickly change throughput)
- Worker count `W`
- Utilization `U` under real incident/retry behavior
- Retention window for `credit_transactions`

Operational recommendation:

- Recompute this model using measured output from `scripts/run_scenarios.py` + monitoring before any production commitment.
