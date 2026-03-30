# RFC-0000: Capacity Model

Parent: [RFC-0000 README](./README.md)

Target: **50,000 customers, 30M task submissions/day**

> **Compute model note:** This model assumes each task is CPU-bound, occupying 1 core for the full task runtime. The assignment code simulates inference with `time.sleep()`, but the capacity model uses the production-realistic assumption: **1 task = 1 compute unit**. This maps directly to GPU-bound inference — replace "CPU core" with "GPU" and the numbers hold.

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
 19      583        3,500    |  ##############       <- before-dinner peak
 20      333        2,000    |  ########
 21      250        1,500    |  ######
 22      133          800    |  ###
 23       83          500    |  ##
```

Total req/s = submit/s x 6 (1 submit + 5 polls per task lifecycle).

---

## Postgres storage

### Per-table growth

| Table                 | Rows/day     | Row size | Daily growth | Retention  | Steady-state |
| --------------------- | ------------ | -------- | ------------ | ---------- | ------------ |
| `tasks`               | 30M          | ~350 B   | 10.5 GB      | 90 days    | 945 GB       |
| `credit_transactions` | 31.2M        | ~150 B   | 4.7 GB       | 365 days   | 1.7 TB       |
| `credit_snapshots`    | 50K (upsert) | ~60 B    | ~0           | 1 row/user | 3 MB         |
| `users`               | 50K          | ~200 B   | ~0           | permanent  | 10 MB        |

`credit_transactions` rows = 30M deductions + 900K cancel refunds + 300K failure refunds.

### With indexes

`tasks` has 5 indexes (PK + 4 composite/partial); `credit_transactions` has 2 indexes (PK + 1 composite).

| Table                        | Data   | Index overhead | Total       |
| ---------------------------- | ------ | -------------- | ----------- |
| `tasks` (90d)                | 945 GB | ~570 GB (~60%) | **~1.5 TB** |
| `credit_transactions` (365d) | 1.7 TB | ~850 GB (~50%) | **~2.5 TB** |
| **Total Postgres**           |        |                | **~4 TB**   |

UUIDv7 primary keys keep B-tree inserts sequential (append-only). No random page splits. `ORDER BY task_id` = `ORDER BY created_at` implicitly.

### Daily storage growth (data + indexes)

| Store              | Component                    | Daily growth | Monthly growth |
| ------------------ | ---------------------------- | ------------ | -------------- |
| Postgres           | `tasks` (data + indexes)     | 16.8 GB      | 504 GB         |
| Postgres           | `credit_transactions` (d+i)  | 7.1 GB       | 213 GB         |
| Postgres           | WAL / bloat overhead (~10%)  | 2.4 GB       | 72 GB          |
| **Postgres total** |                              | **26.3 GB**  | **789 GB**     |
| Redis              | `result:{task_id}` (24h TTL) | net 0        | net 0          |
| Redis              | `idem:*` (24h TTL)           | net 0        | net 0          |
| **Redis total**    |                              | **net 0**    | **net 0**      |

Postgres grows ~26 GB/day. Redis is stable at ~11 GB (TTL-bounded rolling window — new keys replace expired keys).

### Cumulative Postgres disk (data + indexes)

```text
Month   tasks       credit_txn   Total      |  Disk usage
-----   ---------   ----------   ---------  |
  1       504 GB       213 GB      717 GB   |  ##
  2     1,008 GB       426 GB    1,434 GB   |  ###
  3     1,512 GB       639 GB    2,151 GB   |  ####
  4     1,512 GB       852 GB    2,364 GB   |  ####  <- tasks hits 90d
  5     1,512 GB     1,065 GB    2,577 GB   |  #####
  6     1,512 GB     1,278 GB    2,790 GB   |  #####
  9     1,512 GB     1,917 GB    3,429 GB   |  ######
 12     1,512 GB     2,556 GB    4,068 GB   |  #######
 13     1,512 GB     2,556 GB    4,068 GB   |  #######  <- steady-state
```

- `tasks` reaches steady-state at month 3 (90d retention window fills, old rows purged)
- `credit_transactions` reaches steady-state at month 12 (365d retention window fills)
- After month 12, disk is flat at **~4 TB** — purges match inserts

Note: numbers exclude WAL overhead and VACUUM bloat (add ~10% headroom). Provision 4.5 TB to account for transient bloat during bulk deletes.

---

## Redis memory

### Working set

| Key pattern             | Count (24h window) | Size/key     | Total        | TTL      |
| ----------------------- | ------------------ | ------------ | ------------ | -------- |
| `result:{task_id}`      | 30M                | ~250 B       | **7.5 GB**   | 24h      |
| `idem:{user_id}:{key}`  | 30M                | ~120 B       | **3.6 GB**   | 24h      |
| `credits:{user_id}`     | 50K                | ~70 B        | 3.5 MB       | none     |
| `auth:{api_key}`        | ~15K concurrent    | ~200 B       | 3 MB         | 60s      |
| `active:{user_id}`      | ~10K in-flight     | ~70 B        | 700 KB       | none     |
| `credits:dirty`         | <=50K members      | ~70 B/member | 3.5 MB       | none     |
| Celery broker queue     | 500-5K pending     | ~500 B       | 2.5 MB       | consumed |
| `pending:{task_id}`     | 200-500 in-flight  | ~150 B       | 75 KB        | 120s     |
| **Total**               |                    |              | **~11.1 GB** |          |

Two 24h-TTL key pools dominate: result cache (7.5 GB) + idempotency (3.6 GB). Everything else is under 10 MB combined. A single 16 GB Redis instance covers this with headroom for AOF rewrite buffers.

---

## Network transfer

### Per-request breakdown

| Request type       | API-Redis                               | API-Postgres         | Total/req   |
| ------------------ | --------------------------------------- | -------------------- | ----------- |
| Submit (cache hit) | ~1.4 KB (auth + Lua + marker + publish) | ~1 KB (1 txn)        | **~2.5 KB** |
| Poll (cache hit)   | ~450 B (auth + HGETALL)                 | 0                    | **~450 B**  |
| Poll (cache miss)  | ~200 B (auth)                           | ~400 B (SELECT)      | **~600 B**  |
| Cancel             | ~500 B (auth + refund + DECR)           | ~1 KB (SELECT + txn) | **~1.5 KB** |
| Admin credits      | ~300 B (SET + SADD + DEL)               | ~600 B (CTE)         | **~900 B**  |

### Daily aggregate

| Flow      | Calculation             | Daily transfer                                |
| --------- | ----------------------- | --------------------------------------------- |
| Submits   | 30M x 2.5 KB            | 75 GB                                         |
| Polls     | 150M x 450 B            | 67.5 GB                                       |
| Cancels   | 900K x 1.5 KB           | 1.35 GB                                       |
| **Total** | **75 + 67.5 + 1.35 GB** | **~144 GB/day (avg 13 Mbps, peak ~40 Mbps)**  |

All internal Docker network traffic. No egress costs in Compose.

---

## Worker sizing

### Compute model

Each task occupies **1 compute unit** (CPU core) for the full task runtime. No oversubscription.

- **Assignment simulation:** `time.sleep(2.5s)` — the core is idle but reserved
- **Production inference:** 1 GPU per task — the GPU is fully utilized
- The numbers below apply to both. Replace "core" with "GPU" for production.

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
 19      583        1,458      |  ###############          <- peak
 20      333          833      |  ##########
 21      250          625      |  ########
 22      133          333      |  ####
 23       83          208      |  ###
```

### Resource profile per compute unit

| Resource          | Per unit              | Peak (2,083 units) | Trough (63 units) |
| ----------------- | --------------------- | ------------------ | ------------------ |
| CPU / GPU         | 1 core (or 1 GPU)     | 2,083              | 63                 |
| Memory            | ~100 MB (Python + IO) | ~208 GB            | ~6.3 GB            |
| PG connections    | 1                     | 2,083              | 63                 |
| Redis connections | 1                     | 2,083              | 63                 |

At peak, PG connections from workers alone exceed default `max_connections=100`. Production requires PgBouncer or connection pooling middleware in front of Postgres.

### Scaling strategy

**Fixed capacity (Compose):**
Provision 1,200 compute units. Accept queue buildup during peaks.

- Drain rate: 1,200 / 2.5 = 480 tasks/sec
- Handles all hours where submit <= 480/sec
- During peaks (08-10, 16-19), queue absorbs overflow

**Elastic capacity (production):**
Scale compute units based on queue depth signal.

| Trigger                      | Action              | Target        |
| ---------------------------- | ------------------- | ------------- |
| Queue depth > 100 for 30s    | Scale up workers    | +20% capacity |
| Queue depth > 1,000 for 60s  | Scale up workers    | +50% capacity |
| Queue depth = 0 for 5 min    | Scale down workers  | -25% capacity |
| Queue depth = 0 for 15 min   | Scale down to floor | Minimum fleet |

- Minimum fleet: 200 units (drain 80/sec — covers overnight)
- Maximum fleet: 2,500 units (covers peak 2,083 + 20% headroom)

---

## API server sizing

FastAPI with uvicorn is async — a single process handles thousands of concurrent connections. The bottleneck is connection pool fan-out, not CPU.

### Per-instance capacity

| Resource                         | Value                           |
| -------------------------------- | ------------------------------- |
| Concurrent requests per instance | ~2,000-3,000 (async I/O bound)  |
| CPU per instance                 | ~0.5-1 core                     |
| Memory per instance              | ~200 MB                         |
| PG pool per instance             | max 10 connections              |
| Redis pool per instance          | ~10 connections                 |

### Scaling through the day

```text
Hour  Total req/s  API instances  PG connections  Redis connections
----  -----------  -------------  --------------  ----------------
 00          400       2 (HA)             20              20
 03          150       2 (HA)             20              20
 06        1,000       2                  20              20
 08        4,000       3                  30              30
 09        5,000       3                  30              30         <- peak
 12        2,000       2                  20              20
 16        4,500       3                  30              30         <- peak
 19        3,500       2                  20              20
 22          800       2 (HA)             20              20
```

Minimum 2 instances always (HA). Scale to 3 during peak windows. Each instance handles ~2,000 req/s of async I/O. Total PG connection ceiling from API: 30 connections (well within Postgres limits even with worker connections pooled via PgBouncer).

**Scaling signal:** HTTP request latency p99. Scale up when p99 > 200ms sustained for 60s.

---

## Queue depth

Queue depth depends on the gap between submit rate and worker drain capacity.

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

### Elastic-capacity model (200-2,500 compute units, autoscaler)

Fleet targets `submit_rate x 2.5 x 1.2` (demand + 20% headroom). Minimum fleet: 200 units. Scaling lag: ~2 minutes per step. Scale-down hysteresis: 5-15 minutes.

During each hour transition, the fleet scales from its current size to the new target over ~2 minutes. Messages that arrive during the ramp-up form a transient queue that drains once the fleet catches up.

```text
Hour  Submit/s  Fleet   Drain/s  Queue peak   |  Fleet             Queue
----  --------  ------  -------  ----------   |  (block ~ 100u)    (block ~ 2K msgs)
 00       67     200       80          0     |  ##
 01       42     200       80          0     |  ##
 02       25     200       80          0     |  ##
 03       25     200       80          0     |  ##
 04       42     200       80          0     |  ##
 05       83     250      100       ~300     |  ###
 06      167     500      200        ~8K     |  #####              ....
 07      333   1,000      400       ~16K     |  ##########         ........
 08      667   2,000      800       ~32K     |  ################....  <- fleet + queue
 09      833   2,500    1,000        ~4K     |  ####################  ..  <- peak fleet
 10      583   2,500    1,000          0     |  ####################     (oversized)
 11      458   1,750      700          0     |  ################          (scaling down)
 12      333   1,000      400          0     |  ##########
 13      333   1,000      400          0     |  ##########
 14      417   1,250      500        ~2K     |  ############# .
 15      458   1,375      550        ~1K     |  ##############
 16      750   2,250      900       ~24K     |  ################### ....
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

| Metric                | Fixed (1,200 units) | Elastic (200-2,500)    |
| --------------------- | ------------------- | ---------------------- |
| Peak queue depth      | 3.55M messages      | ~32K messages          |
| Worst-case wait       | 2.1 hours           | ~4 minutes             |
| Queue clears by       | ~11:40pm            | within 4-6 min         |
| Peak Redis for queue  | 1.78 GB             | ~16 MB                 |
| Avg fleet utilization | 72%                 | 77%                    |
| Min compute (night)   | 1,200 units 24/7    | 200 units              |
| Peak compute          | 1,200 units 24/7    | 2,500 units (hour 09)  |

Utilization math:

- Total task-hours/day: 30M tasks x 2.5s / 3,600 = 20,833 unit-hours
- Fixed: 1,200 units x 24h = 28,800 unit-hours. Utilization = 72%
- Elastic: sum of hourly fleet = 27,025 unit-hours. Utilization = 77%
- Real win: overnight fleet drops from 1,200 to 200 (83% compute savings during low-traffic hours)

### Queue memory impact

| Model   | Peak queue | Queue memory | Redis total at peak |
| ------- | ---------- | ------------ | ------------------- |
| Fixed   | 3.55M msgs | 1.78 GB      | 12.9 GB             |
| Elastic | ~32K msgs  | ~16 MB       | 11.1 GB             |

Both fit within a single 16 GB Redis instance.

### Queue latency at peak

**Fixed model:** Worst-case queue wait = 3.55M / 480 = **2.1 hours**. Tasks submitted at 7pm clear around 9pm.

**Elastic model:** Worst-case transient wait = 32K / (800-667) = 240s = **~4 minutes**. Transient queue from 07->08 transition drains by 08:06.

---

## Infrastructure summary

### Compose deployment (fixed capacity, single host)

| Component     | Instances    | CPU / GPU        | Memory      | Disk      |
| ------------- | ------------ | ---------------- | ----------- | --------- |
| API           | 2            | 2 cores          | 400 MB      | -         |
| Workers (C=1) | 1,200        | 1,200 cores      | 120 GB      | -         |
| Redis         | 1            | 2 cores          | 16 GB       | 1 GB AOF  |
| Postgres      | 1            | 4 cores          | 8 GB        | 4 TB SSD  |
| Celery broker | shared Redis | -                | -           | -         |
| Prometheus    | 1            | 1 core           | 2 GB        | 50 GB     |
| Grafana       | 1            | 0.5 cores        | 512 MB      | -         |
| **Total**     |              | **~1,210 cores** | **~147 GB** | **~4 TB** |

Note: 1,200 workers on a single Compose host is unrealistic. This model shows what the workload demands; production uses orchestration (K8s, ECS) across multiple nodes.

### Production deployment (elastic, multi-node)

| Component | Min                    | Max   | Scaling signal               |
| --------- | ---------------------- | ----- | ---------------------------- |
| API       | 2                      | 5     | HTTP p99 latency > 200ms     |
| Workers   | 200                    | 2,500 | Queue depth thresholds       |
| Redis     | 1 primary + 2 replicas | same  | Memory > 80% -> vertical     |
| Postgres  | 1 primary + 1 replica  | same  | Connection saturation, IOPS  |
| PgBouncer | 2 (HA)                 | same  | Worker connection fan-out    |

### Cost drivers (ranked)

1. **Workers** — 90%+ of compute cost. At peak: 2,083 cores (or GPUs). The dominant cost by far.
2. **Postgres disk** — 4 TB SSD at steady-state. Dominated by `credit_transactions` (365d retention).
3. **Redis memory** — 16 GB. Stable regardless of customer growth (driven by 24h TTL window, not total users).
4. **Network** — 144 GB/day internal. Negligible cost.
5. **API** — 2-3 instances. Trivial compared to workers.
