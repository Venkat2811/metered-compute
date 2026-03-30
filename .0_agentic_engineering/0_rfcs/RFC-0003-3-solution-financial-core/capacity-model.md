# RFC-0003: Capacity Model

Parent: [RFC-0003 README](./README.md)

Target: **50,000 customers, 30M task submissions/day**

> **Compute model note:** This model assumes each task is CPU-bound, occupying 1 compute unit for the full task runtime. The assignment code simulates inference with `time.sleep()`, but the capacity model uses the production-realistic assumption: **1 task = 1 compute unit**. This maps directly to GPU-bound inference — replace "CPU core" with "GPU" and the numbers hold.

> **Key difference vs solutions 0-2:** TigerBeetle replaces app-coordinated billing SQL. Redpanda provides a replayable event backbone (retained log, independent consumers). RabbitMQ is retained but narrowed to worker dispatch with hot/cold model-affinity routing. Redis is a query cache + active counters + warm-model registry (smallest footprint of all solutions). `T_avg` uses a weighted model-class blend: 60% small@2s + 30% medium@4s + 10% large@6s = **2.8s** for production sizing. The compose-scale analytical model uses `W=8, C=6, T=2.0s` for baseline throughput estimation.

---

## Workload profile

| Parameter              | Value                                           |
| ---------------------- | ----------------------------------------------- |
| Total customers        | 50,000                                          |
| Daily task submissions | 30,000,000                                      |
| Avg polls per task     | 5                                               |
| Cancel rate            | 3%                                              |
| Worker failure rate    | ~1% (assumption)                                |
| Task runtime (average) | 2.8s (weighted: 60% small + 30% medium + 10% large) |

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

| Table                        | Rows/day     | Row size | Daily growth | Retention               | Steady-state |
| ---------------------------- | ------------ | -------- | ------------ | ----------------------- | ------------ |
| `cmd.task_commands`          | 30M          | ~400 B   | 12.0 GB      | 90 days                 | 1.08 TB      |
| `cmd.outbox_events`         | 30M+         | ~300 B   | 9.0 GB       | 24h (purge after relay) | 9 GB         |
| `cmd.inbox_events`          | ~30M         | ~100 B   | 3.0 GB       | 7 days (dedup window)   | 21 GB        |
| `query.task_query_view`     | 30M          | ~350 B   | 10.5 GB      | 120 days                | 1.26 TB      |
| `cmd.billing_reconcile_jobs`| ~1K          | ~150 B   | ~150 KB      | 90 days                 | ~14 MB       |
| `cmd.projection_checkpoints`| ~4 (upsert)  | ~100 B   | ~0           | 1 row/projector         | <1 KB        |
| `users`                     | 50K          | ~200 B   | ~0           | permanent               | 10 MB        |
| `api_keys`                  | 50K          | ~120 B   | ~0           | permanent               | 6 MB         |

`cmd.task_commands` rows are ~400 B (larger than Sol 0 `tasks` at ~350 B) because of additional columns: `tb_pending_transfer_id`, `billing_state`, `model_class`, `callback_url`, `idempotency_key`.

`cmd.outbox_events` are ephemeral: the relay publishes events to Redpanda, then marks them `published_at`. A purge job deletes published rows older than 24h. Effective steady-state is ~1 day's worth of events.

`cmd.inbox_events` stores only `(event_id, consumer_name, processed_at)` for dedup. 7-day retention covers any replay window. Rows are minimal (~100 B).

`cmd.billing_reconcile_jobs` grows slowly -- only stale pending transfers generate rows. At ~1% failure rate with 600s timeout, roughly ~1K rows/day in the worst case. Most resolve quickly.

`cmd.projection_checkpoints` is tiny: one row per projector/consumer (projector, dispatcher, webhook-worker, event-exporter). Updated in place, never grows.

`api_keys` uses SHA-256 hashed keys (`CHAR(64)` primary key) -- no plaintext storage.

### With indexes

`cmd.task_commands` has 4 indexes (PK + user_idem unique + billing_state partial + user_status); `cmd.outbox_events` has 2 indexes (PK + unpublished partial); `cmd.inbox_events` has 1 index (PK); `query.task_query_view` has 3 indexes (PK + user_updated + status partial); `cmd.billing_reconcile_jobs` has 2 indexes (PK + state partial); `cmd.projection_checkpoints` has 2 indexes (PK + topic_partition).

| Table                                   | Data    | Index overhead | Total       |
| --------------------------------------- | ------- | -------------- | ----------- |
| `cmd.task_commands` (90d)               | 1.08 TB | ~650 GB (~60%) | **~1.7 TB** |
| `query.task_query_view` (120d)          | 1.26 TB | ~630 GB (~50%) | **~1.9 TB** |
| `cmd.outbox_events` (24h)               | 9 GB    | ~5 GB (~55%)   | **~14 GB**  |
| `cmd.inbox_events` (7d)                 | 21 GB   | ~10 GB (~50%)  | **~31 GB**  |
| `cmd.billing_reconcile_jobs` (90d)      | ~14 MB  | ~7 MB (~50%)   | **~21 MB**  |
| `cmd.projection_checkpoints`            | <1 KB   | <1 KB          | **<2 KB**   |
| Other tables (`users`, `api_keys`)      | <20 MB  | <20 MB         | **<40 MB**  |
| **Total Postgres**                      |         |                | **~3.6 TB** |

UUIDv7 primary keys keep B-tree inserts sequential (append-only). No random page splits. `ORDER BY task_id` = `ORDER BY created_at` implicitly.

Note: Sol 3 has less PG disk than Sol 2 (~3.6 TB vs ~7.7 TB) because TigerBeetle handles billing data that Sol 2 stores in `credit_reservations` (1.6 TB) and `credit_transactions` (2.5 TB). The tradeoff: billing data lives in TigerBeetle's own storage format instead of Postgres.

### Daily storage growth (data + indexes)

| Store              | Component                         | Daily growth | Monthly growth |
| ------------------ | --------------------------------- | ------------ | -------------- |
| Postgres           | `cmd.task_commands` (data + idx)  | 19.2 GB      | 576 GB         |
| Postgres           | `query.task_query_view` (d+i)     | 15.8 GB      | 474 GB         |
| Postgres           | `cmd.outbox_events` (24h window)  | net 0        | net 0          |
| Postgres           | `cmd.inbox_events` (7d window)    | net 0        | net 0          |
| Postgres           | WAL / bloat overhead (~10%)       | 3.5 GB       | 105 GB         |
| **Postgres total** |                                   | **38.5 GB**  | **1,155 GB**   |
| Redis              | `task:{task_id}` (24h TTL)        | net 0        | net 0          |
| **Redis total**    |                                   | **net 0**    | **net 0**      |
| RabbitMQ           | Queue messages (consumed=gone)    | net 0        | net 0          |
| **RabbitMQ total** |                                   | **net 0**    | **net 0**      |

Postgres grows ~38.5 GB/day (vs ~26 GB/day in Sol 0, ~56 GB/day in Sol 2). The reduction from Sol 2 is entirely because `credit_reservations` and `credit_transactions` are no longer in Postgres -- TigerBeetle owns billing data. Redis is stable at ~5-6 GB (TTL-bounded). RabbitMQ is flow-through (consumed messages are gone).

`outbox_events` and `inbox_events` show net 0 growth because they are bounded by short retention windows (24h and 7d respectively). After their retention windows fill, purges match inserts.

### Cumulative Postgres disk (data + indexes)

```text
Month   task_cmds   query_view  Total      |  Disk usage
-----   ---------   ----------  ------     |
  1      576 GB      474 GB     1,050 GB   |  ###
  2    1,152 GB      948 GB     2,100 GB   |  #######
  3    1,728 GB    1,422 GB     3,150 GB   |  ##########  <- task_cmds 90d
  4    1,728 GB    1,896 GB     3,624 GB   |  ############  <- query_view 120d
  5    1,728 GB    1,896 GB     3,624 GB   |  ############  <- steady-state
```

- `cmd.task_commands` reaches steady-state at month 3 (90d retention window fills)
- `query.task_query_view` reaches steady-state at month 4 (120d retention window fills)
- After month 4, disk is flat at **~3.6 TB** -- purges match inserts
- `outbox_events` (24h) and `inbox_events` (7d) reach steady-state in week 1 and are negligible (~45 GB combined)
- `billing_reconcile_jobs` is negligible (~21 MB at 90d steady-state)

Note: numbers exclude WAL overhead and VACUUM bloat (add ~10% headroom). Provision 4 TB to account for transient bloat during bulk deletes. Sol 3 requires roughly **0.9x the disk of Sol 0** (~3.6 TB vs ~4 TB) and **0.5x the disk of Sol 2** (~3.6 TB vs ~7.7 TB), because TigerBeetle absorbs all billing storage.

---

## TigerBeetle storage

TigerBeetle is purpose-built for double-entry accounting. It stores accounts and transfers in a custom on-disk format optimized for append-only ledger operations.

### Account storage

| Account type         | Count        | Size/account | Total     |
| -------------------- | ------------ | ------------ | --------- |
| User credit accounts | 50,000       | ~300 B       | ~15 MB    |
| Platform revenue     | 1            | ~300 B       | ~300 B    |
| Escrow               | 1            | ~300 B       | ~300 B    |
| **Total accounts**   | **50,002**   |              | **~15 MB**|

Account storage is effectively static. New users add accounts, but at 50K users the total is negligible.

### Transfer storage

Each task lifecycle generates 2 transfers: a pending transfer (reserve) and a post or void (capture or release).

| Transfer type          | Daily count | Size/transfer | Daily growth |
| ---------------------- | ----------- | ------------- | ------------ |
| Pending (reserve)      | 30M         | ~128 B        | 3.84 GB      |
| Post (capture, ~96%)   | 28.8M       | ~128 B        | 3.69 GB      |
| Void (cancel+fail, ~4%)| 1.2M        | ~128 B        | 0.15 GB      |
| **Total transfers**    | **60M**     |               | **7.68 GB**  |

### Cumulative TigerBeetle disk

TigerBeetle's ledger is append-only -- transfers are never deleted or compacted.

```text
Month   Transfers    Cumulative disk  |  Disk usage
-----   ----------   ---------------  |
  1     1.8B          230 GB          |  ####
  3     5.4B          691 GB          |  ###########
  6    10.8B        1,382 GB          |  ######################
 12    21.6B        2,765 GB          |  ############################################
 24    43.2B        5,530 GB          |  ########################################################################################
```

- TigerBeetle grows ~7.7 GB/day (~230 GB/month) indefinitely
- At 12 months: ~2.8 TB. At 24 months: ~5.5 TB.
- No retention pruning is available -- this is a ledger. Production deployments archive historical data to cold storage or provision accordingly.
- TigerBeetle's storage format is highly efficient: fixed-size structs, no JSON, no indexing overhead beyond the primary key.

### Throughput headroom

TigerBeetle handles ~1M transfers/sec on commodity hardware. Sol 3 peak demand is ~2,080 transfers/sec (1,040 submits x 2 transfers). **TigerBeetle operates at 0.2% of its rated capacity. It will never be the bottleneck.**

---

## Redis memory

### Working set

| Key pattern           | Count (24h window) | Size/key | Total      | TTL  |
| --------------------- | ------------------ | -------- | ---------- | ---- |
| `task:{task_id}`      | 30M                | ~200 B   | **6.0 GB** | 24h  |
| Rate limit counters   | ~50K               | ~80 B    | 4 MB       | 60s  |
| `active:{uid}`        | ~10K in-flight     | ~70 B    | 700 KB     | none |
| `warm:{model_class}`  | 3 sets, ~400 members each | ~70 B/member | ~84 KB | none |
| `revoked:{uid}:{d}`   | ~10K entries/day   | ~70 B    | <1 MB      | 36h  |
| `auth:{api_key}`      | ~15K concurrent    | ~200 B   | 3 MB       | 60s  |
| **Total**             |                    |          | **~6 GB**  |      |

vs Sol 0 (~11.1 GB), Sol 1 (~14.2 GB), Sol 2 (~6 GB): Redis memory is the smallest of all solutions because:

- **No `result:{task_id}` hashes** -- the `task:{task_id}` hash stores status + result in one key (~200 B combined)
- **No `idem:{user_id}:{key}` keys** -- idempotency is enforced by a Postgres unique constraint on `(user_id, idempotency_key)`, not Redis
- **No `credits:{user_id}` keys** -- credit balances live in TigerBeetle (not Redis or Postgres)
- **No `credits:dirty` set** -- no credit drift reaper needed (TigerBeetle is the authority)
- **No Celery broker queue / Redis Streams** -- task queue is in RabbitMQ
- **No `pending:{task_id}` markers** -- RabbitMQ handles in-flight tracking via ack/nack
- **`warm:{model_class}` sets are tiny** -- 3 sets (small, medium, large) with ~400 worker IDs each

A single **8 GB Redis instance** covers this with ample headroom for AOF rewrite buffers. This is half the memory of Sol 0 (16 GB) and a quarter of Sol 1 fixed-model (32 GB).

---

## Redpanda storage

Redpanda provides the replayable event backbone. Unlike RabbitMQ (consumed = gone), Redpanda retains events for the configured retention window.

### Topic volumes

| Topic               | Messages/day | Avg msg size | Daily data | Purpose                        |
| -------------------- | ------------ | ------------ | ---------- | ------------------------------ |
| `tasks.requested`    | 30M          | ~500 B       | 15.0 GB    | Submit events -> dispatcher    |
| `tasks.completed`    | 28.8M        | ~500 B       | 14.4 GB    | Success -> projector, webhook  |
| `tasks.failed`       | 300K         | ~500 B       | 0.15 GB    | Failure -> projector, webhook  |
| `tasks.cancelled`    | 900K         | ~500 B       | 0.45 GB    | Cancel -> projector            |
| `billing.captured`   | 28.8M        | ~300 B       | 8.64 GB    | Revenue event -> analytics     |
| `billing.released`   | 1.2M         | ~300 B       | 0.36 GB    | Refund event -> analytics      |
| **Total**            | **~90M**     |              | **~39 GB** |                                |

Each task generates ~2 events minimum (requested + terminal state) plus ~2 billing events (captured or released). Some tasks generate `tasks.started` as well, but this is omitted from sizing as it adds negligible volume.

### Retention and disk

| Retention | Daily data | Steady-state disk |
| --------- | ---------- | ----------------- |
| 7 days    | ~39 GB     | **~273 GB**       |

Redpanda uses segment-based log storage with per-topic retention. After 7 days, old segments are deleted. Steady-state disk is bounded at ~273 GB.

Provision **300 GB SSD** for Redpanda to account for segment compaction overhead and replication buffer.

### Throughput headroom

Redpanda single-node sustains ~100K msg/sec. Sol 3 peak demand is ~2,080 msg/sec (1,040 submits x 2 events per task lifecycle). **Redpanda operates at ~2% of its rated capacity. It will not be the bottleneck.**

---

## RabbitMQ memory

RabbitMQ's role in Sol 3 is narrowed to worker dispatch only. It does not carry the event path (Redpanda does that). Messages are transient -- consumed and acknowledged by workers.

### Queue topology

| Queue         | Purpose                              | Consumer            | Message TTL |
| ------------- | ------------------------------------ | ------------------- | ----------- |
| `hot-small`   | Warm workers with small model loaded | Workers (warm)      | 5 min       |
| `hot-medium`  | Warm workers with medium model       | Workers (warm)      | 5 min       |
| `hot-large`   | Warm workers with large model        | Workers (warm)      | 5 min       |
| `cold`        | Cold pool (loads any model)          | Workers (cold)      | 30 min      |
| `preloaded`   | Headers exchange, routes to hot-*    | N/A (exchange)      | N/A         |
| `coldstart`   | Alternate exchange, fallback to cold | N/A (exchange)      | N/A         |

### Queue depth and memory

At **fixed capacity** (1,200 compute units, drain = 429/sec at T_avg=2.8s), queue depth builds during peaks:

| Metric                   | Value                         |
| ------------------------ | ----------------------------- |
| Peak queue depth         | ~4.19M messages (at 7pm)      |
| Message size (avg)       | ~500 B (event payload + hdrs) |
| Peak queue memory        | ~2.1 GB                       |
| RabbitMQ overhead        | ~2 GB (mgmt, flow control)    |
| **RabbitMQ recommended** | **4 GB**                      |

At **elastic capacity** (200-2,500 units), peak queue depth is ~32K messages (~16 MB). RabbitMQ runs comfortably on 2 GB.

RabbitMQ is designed for flow-through, not storage. Unlike Redpanda, consumed messages are gone. The hot/cold routing adds no storage overhead -- it is purely a routing decision at publish time via header exchange matching.

### Memory breakdown at peak (fixed model)

| Component                 | Memory    |
| ------------------------- | --------- |
| Queue messages (4.19M)    | 2.1 GB    |
| Message indices           | ~250 MB   |
| Management plugin         | ~200 MB   |
| Erlang VM overhead        | ~500 MB   |
| Flow control buffers      | ~300 MB   |
| **Total RabbitMQ memory** | **~3.3 GB** |

Provision a 4 GB RabbitMQ instance for the fixed model. The elastic model runs on 2 GB.

---

## Network transfer

### Per-request breakdown

| Request type      | API-Redis                     | API-Postgres                       | API-TigerBeetle     | Total/req   |
| ----------------- | ----------------------------- | ---------------------------------- | -------------------- | ----------- |
| Submit            | ~300 B (cache write-through)  | ~2.0 KB (txn: cmd + outbox)        | ~300 B (pending txfr)| **~2.6 KB** |
| Poll (cache hit)  | ~450 B (revocation + HGETALL) | 0                                  | 0                    | **~450 B**  |
| Poll (cache miss) | ~200 B (revocation)           | ~400 B (SELECT query view)         | 0                    | **~600 B**  |
| Cancel            | ~200 B (cache update)         | ~1.5 KB (txn: update + outbox)     | ~200 B (void txfr)   | **~1.9 KB** |
| Admin credits     | 0                             | ~600 B (audit row)                 | ~200 B (direct txfr) | **~800 B**  |

Submit is slightly lighter on PG than Sol 2 (~2.0 KB vs ~2.5 KB) because there is no `credit_reservations` INSERT. The TigerBeetle hop adds ~300 B network transfer but replaces the PG reservation row entirely.

### Internal flows (not client-facing)

| Flow                          | Calculation                  | Per-event  | Daily transfer |
| ----------------------------- | ---------------------------- | ---------- | -------------- |
| Outbox relay -> Redpanda      | 30M events x 500 B           | ~500 B     | 15 GB          |
| Outbox relay -> PG            | 30M UPDATEs (mark published) | ~100 B     | 3 GB           |
| Redpanda -> Dispatcher        | 30M messages x 500 B         | ~500 B     | 15 GB          |
| Dispatcher -> RabbitMQ        | 30M messages x 500 B         | ~500 B     | 15 GB          |
| RabbitMQ -> Workers           | 30M messages x 500 B         | ~500 B     | 15 GB          |
| Workers -> TB (post/void)     | 30M transfers x 200 B        | ~200 B     | 6 GB           |
| Workers -> PG (completion)    | 30M txns x 1.5 KB            | ~1.5 KB    | 45 GB          |
| Workers -> Redis (cache)      | 30M cache writes x 200 B     | ~200 B     | 6 GB           |
| Redpanda -> Projector         | 30M events x 500 B           | ~500 B     | 15 GB          |
| Projector -> PG               | 30M upserts x 400 B          | ~400 B     | 12 GB          |
| Reconciler -> TB + PG         | marginal (stale scan)        | negligible | < 1 GB         |

### Daily aggregate

| Flow                  | Calculation                 | Daily transfer                               |
| --------------------- | --------------------------- | -------------------------------------------- |
| Client-facing submits | 30M x 2.6 KB               | 78 GB                                        |
| Client-facing polls   | 150M x 450 B               | 67.5 GB                                      |
| Client-facing cancels | 900K x 1.9 KB              | 1.7 GB                                       |
| Internal flows        | relay + dispatcher + workers + projector | 148 GB                          |
| **Total**             | **147 + 148 GB**            | **~295 GB/day (avg 27 Mbps, peak ~82 Mbps)** |

All internal Docker network traffic. No egress costs in Compose. Daily transfer is ~2x Sol 0 (~144 GB) and ~1.2x Sol 2 (~250 GB) because the Redpanda -> Dispatcher -> RabbitMQ bridge adds an extra hop that does not exist in Sol 2's direct outbox-to-RabbitMQ model. The Redpanda fan-out to projector, webhook, and analytics also adds per-consumer network transfer.

---

## Worker sizing

### Compute model

Each task occupies **1 compute unit** (CPU core) for the full task runtime. No oversubscription.

- **Assignment simulation:** `time.sleep(model_factor * 2)` -- the core is idle but reserved
- **Production inference:** 1 GPU per task -- the GPU is fully utilized
- The numbers below apply to both. Replace "core" with "GPU" for production.

Workers consume from RabbitMQ via hot/cold model-affinity routing. Each worker handles 1 concurrent task. RabbitMQ prefetch count = 1 (no message buffering).

### Model-class weighted runtime

Sol 3 introduces model-class-aware dispatch. Task runtimes vary by model:

| Model class | Runtime | Traffic share | Weighted contribution |
| ----------- | ------- | ------------- | --------------------- |
| Small       | 2s      | 60%           | 1.2s                  |
| Medium      | 4s      | 30%           | 1.2s                  |
| Large       | 6s      | 10%           | 0.6s                  |
| **T_avg**   |         |               | **3.0s**              |

Cold start penalty: 3s model load time (first task per model per worker). With warm model cache, most tasks hit the hot path (no load penalty). Effective T_avg for a warmed fleet: **~2.8s** (accounting for ~5% cold-start rate at steady state).

This is better than Sol 2's uniform 2.5s assumption because the hot/cold routing preferentially sends tasks to workers that already have the correct model loaded, avoiding the cold-start penalty for the majority of tasks.

### Compute demand throughout the day

Compute units needed = `submit_rate x T_avg`. Each worker process handles 1 concurrent task (C=1).

```text
Hour  Submit/s  Compute units  |  Worker demand
----  --------  -------------  |
 00       67          188      |  ##
 01       42          118      |  #
 02       25           70      |  #
 03       25           70      |  #
 04       42          118      |  #
 05       83          232      |  ###
 06      167          468      |  #####
 07      333          932      |  ##########
 08      667        1,868      |  ####################
 09      833        2,332      |  #########################  <- peak
 10      583        1,632      |  #################
 11      458        1,282      |  ##############
 12      333          932      |  ##########
 13      333          932      |  ##########
 14      417        1,168      |  ############
 15      458        1,282      |  ##############
 16      750        2,100      |  ######################
 17      750        2,100      |  ######################     <- peak
 18      583        1,632      |  #################
 19      583        1,632      |  #################          <- peak
 20      333          932      |  ##########
 21      250          700      |  ########
 22      133          372      |  ####
 23       83          232      |  ###
```

Peak compute demand is 2,332 units (vs 2,083 in Sol 0/2 at T_avg=2.5s). The higher T_avg from the weighted model mix increases peak demand by ~12%.

### Resource profile per compute unit

| Resource             | Per unit              | Peak (2,332 units) | Trough (70 units) |
| -------------------- | --------------------- | ------------------ | ----------------- |
| CPU / GPU            | 1 core (or 1 GPU)    | 2,332              | 70                |
| Memory               | ~100 MB (Python + IO) | ~233 GB            | ~7 GB             |
| PG connections       | 1                     | 2,332              | 70                |
| RabbitMQ connections | 1                     | 2,332              | 70                |
| TB connections       | shared (pool)         | ~50 (pooled)       | ~10               |

At peak, PG connections from workers alone exceed default `max_connections=100`. Production requires PgBouncer or connection pooling middleware in front of Postgres.

Note: workers connect to RabbitMQ for task consumption, Postgres for command DB updates, TigerBeetle for post/void transfers, and Redis for cache write-through. Each worker holds 1 RabbitMQ connection with 1 channel. RabbitMQ handles 2,332 concurrent connections comfortably (Erlang is designed for this). TigerBeetle connections are pooled -- a shared client with ~50 connections handles all worker transfer operations.

### Scaling strategy

**Fixed capacity (Compose):**
Provision 1,200 compute units. Accept queue buildup during peaks.

- Drain rate: 1,200 / 2.8 = 429 tasks/sec
- Handles all hours where submit <= 429/sec
- During peaks (08-10, 16-19), RabbitMQ queues absorb overflow

**Elastic capacity (production):**
Scale compute units based on RabbitMQ queue depth signal.

| Trigger                     | Action              | Target        |
| --------------------------- | ------------------- | ------------- |
| Queue depth > 100 for 30s   | Scale up workers    | +20% capacity |
| Queue depth > 1,000 for 60s | Scale up workers    | +50% capacity |
| Queue depth = 0 for 5 min   | Scale down workers  | -25% capacity |
| Queue depth = 0 for 15 min  | Scale down to floor | Minimum fleet |

- Minimum fleet: 200 units (drain 71/sec -- covers overnight)
- Maximum fleet: 2,800 units (covers peak 2,332 + 20% headroom)

Queue depth signal comes from RabbitMQ management API (`/api/queues` endpoint) or Prometheus metrics via the `rabbitmq_prometheus` plugin. Hot/cold queue depth can be monitored separately -- if `cold` queue grows while `hot-*` queues are empty, it indicates a model-class mismatch in the fleet.

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
| TB pool per instance             | ~5 connections                 |

### Scaling through the day

```text
Hour  Total req/s  API instances  PG connections  Redis connections  TB connections
----  -----------  -------------  --------------  ----------------  --------------
 00          400        2 (HA)            20              20              10
 03          150        2 (HA)            20              20              10
 06        1,000        2                 20              20              10
 08        4,000        3                 30              30              15
 09        5,000        3                 30              30              15       <- peak
 12        2,000        2                 20              20              10
 16        4,500        3                 30              30              15       <- peak
 19        3,500        2                 20              20              10
 22          800        2 (HA)            20              20              10
```

Minimum 2 instances always (HA). Scale to 3 during peak windows. Each instance handles ~2,000 req/s of async I/O. Total PG connection ceiling from API: 30 connections (well within Postgres limits even with worker connections pooled via PgBouncer).

Note: API TigerBeetle connections are used only on submit (pending transfer) and cancel (void transfer). TB connection pool is small (~5 per instance) because TB operations complete in <1ms.

**Scaling signal:** HTTP request latency p99. Scale up when p99 > 200ms sustained for 60s.

---

## Queue depth

Queue depth depends on the gap between submit rate and worker drain capacity. In Sol 3, the dispatch queue lives in RabbitMQ (hot/cold queues). Redpanda retains events independently but the dispatch queue depth drives worker scaling.

### Fixed-capacity model (1,200 compute units, drain = 429 tasks/sec)

```text
Hour  Submit/s  Drain/s  Net/s   Queue depth  |  Queue (each block ~ 100K msgs)
----  --------  -------  ------  -----------  |
 00       67      429    -362           0     |
 01       42      429    -387           0     |
 02       25      429    -404           0     |
 03       25      429    -404           0     |
 04       42      429    -387           0     |
 05       83      429    -346           0     |
 06      167      429    -262           0     |
 07      333      429     -96           0     |
 08      667      429    +238       857K     |  #########
 09      833      429    +404      2.31M     |  ####################
 10      583      429    +154      2.86M     |  ########################
 11      458      429     +29      2.97M     |  #########################
 12      333      429     -96      2.62M     |  ######################
 13      333      429     -96      2.27M     |  ###################
 14      417      429     -12      2.23M     |  ###################
 15      458      429     +29      2.33M     |  ####################
 16      750      429    +321      3.49M     |  ##############################
 17      750      429    +321      4.64M     |  ########################################
 18      583      429    +154      5.19M     |  #############################################
 19      583      429    +154      5.75M     |  ################################################# <- peak
 20      333      429     -96      5.41M     |  ##############################################
 21      250      429    -179      4.76M     |  #########################################
 22      133      429    -296      3.70M     |  ################################
 23       83      429    -346      2.45M     |  #####################
```

Note: at T_avg=2.8s, the fixed fleet of 1,200 only drains 429/sec (vs 480/sec in Sol 0/2 at 2.5s). This means the queue does NOT fully clear overnight. The morning residual is ~1.2M messages:

```text
 00       67      429    -362      1.20M     |  <- residual from prior day
```

The queue accumulates ~1.2M overnight residual in steady-state. To clear fully overnight, the fixed fleet would need ~1,400 units (drain = 500/sec). Alternatively, the elastic model eliminates this problem entirely.

### Elastic-capacity model (200-2,800 compute units, autoscaler)

Fleet targets `submit_rate x 2.8 x 1.2` (demand + 20% headroom). Minimum fleet: 200 units. Scaling lag: ~2 minutes per step. Scale-down hysteresis: 5-15 minutes.

```text
Hour  Submit/s  Fleet   Drain/s  Queue peak  |  Fleet             Queue
----  --------  ------  -------  ----------  |  (block ~ 100u)    (block ~ 2K msgs)
 00       67     200       71          0     |  ##
 01       42     200       71          0     |  ##
 02       25     200       71          0     |  ##
 03       25     200       71          0     |  ##
 04       42     200       71          0     |  ##
 05       83     280      100       ~300     |  ###
 06      167     560      200        ~8K     |  ######             ....
 07      333   1,120      400       ~16K     |  ###########        ........
 08      667   2,240      800       ~32K     |  ##################....  <- fleet + queue
 09      833   2,800    1,000        ~4K     |  ########################  ..  <- peak fleet
 10      583   2,800    1,000          0     |  ########################     (oversized)
 11      458   1,960      700          0     |  ####################         (scaling down)
 12      333   1,120      400          0     |  ###########
 13      333   1,120      400          0     |  ###########
 14      417   1,400      500        ~2K     |  ##############  .
 15      458   1,540      550        ~1K     |  ################
 16      750   2,520      900       ~24K     |  ######################  ....
 17      750   2,520      900          0     |  ######################       (fleet steady)
 18      583   2,520      900          0     |  ######################       (scaling down)
 19      583   1,960      700          0     |  ####################
 20      333   1,120      400          0     |  ###########
 21      250     840      300          0     |  #########
 22      133     450      160          0     |  #####
 23       83     280      100          0     |  ###
```

Transient queue math (worst transitions):

- **07->08**: fleet 1,120 (drain=400), submit jumps to 667. Overflow = 267/sec x 120s = **~32K**. Drains in ~4 min once fleet reaches 2,240.
- **15->16**: fleet 1,540 (drain=550), submit jumps to 750. Overflow = 200/sec x 120s = **~24K**. Drains in ~3 min once fleet reaches 2,520.
- **08->09**: fleet already 2,240 (drain=800), submit rises to 833. Overflow = 33/sec x 120s = **~4K**. Drains in seconds.

### Fixed vs elastic comparison

| Metric                  | Fixed (1,200 units) | Elastic (200-2,800)   |
| ----------------------- | ------------------- | --------------------- |
| Peak queue depth        | 5.75M messages      | ~32K messages         |
| Worst-case wait         | 3.7 hours           | ~4 minutes            |
| Queue clears overnight  | NO (1.2M residual)  | Yes                   |
| Peak RabbitMQ for queue | 2.9 GB              | ~16 MB                |
| Avg fleet utilization   | 68%                 | 77%                   |
| Min compute (night)     | 1,200 units 24/7    | 200 units             |
| Peak compute            | 1,200 units 24/7    | 2,800 units (hour 09) |

Utilization math:

- Total task-hours/day: 30M tasks x 2.8s / 3,600 = 23,333 unit-hours
- Fixed: 1,200 units x 24h = 28,800 unit-hours. Utilization = 81% (but queue never clears -- system is undersized)
- Elastic: sum of hourly fleet = 30,240 unit-hours. Utilization = 77%
- Real win: overnight fleet drops from 1,200 to 200 (83% compute savings during low-traffic hours)

Note: the fixed 1,200-unit fleet is undersized for Sol 3's T_avg=2.8s. It would need ~1,400 units (drain=500/sec) to fully clear overnight. This is the tradeoff of model-class-weighted runtimes: higher average task time requires more compute or accepting deeper queues.

### Queue memory impact

| Model   | Peak queue | Queue store | Queue memory | Redis total | RabbitMQ total |
| ------- | ---------- | ----------- | ------------ | ----------- | -------------- |
| Fixed   | 5.75M msgs | RabbitMQ    | 2.9 GB       | 6 GB        | ~5 GB          |
| Elastic | ~32K msgs  | RabbitMQ    | ~16 MB       | 6 GB        | ~1 GB          |

Key difference from Sol 0: queue memory is in RabbitMQ, not Redis. Redis stays at a constant ~6 GB regardless of queue buildup. Redpanda retains events independently for 7 days regardless of queue state -- its storage is not affected by worker drain rate.

### Queue latency at peak

**Fixed model:** Worst-case queue wait = 5.75M / 429 = **3.7 hours**. Tasks submitted at 7pm clear around 10:40pm. Queue does not fully clear overnight.

**Elastic model:** Worst-case transient wait = 32K / (800-667) = 240s = **~4 minutes**. Transient queue from 07->08 transition drains by 08:06.

**Model-affinity queue behavior:** With hot/cold routing, wait times vary by model class and fleet composition:
- Tasks for warmed model classes: routed to `hot-{class}` queues, consumed immediately by warm workers
- Tasks for cold model classes: fall through to `cold` queue, incur 3s load penalty
- At steady state with a balanced fleet, ~95% of tasks hit the hot path (no cold-start penalty)
- During fleet scale-up, new workers start cold and transition to warm after their first task

---

## ClickHouse storage (optional analytics profile)

ClickHouse is included as an optional Compose profile (`--profile analytics`). It receives business events from Redpanda via an event exporter and provides OLAP queries for Grafana dashboards.

### Events table

| Column        | Type                    | Size contribution |
| ------------- | ----------------------- | ----------------- |
| event_id      | UUID                    | 16 B              |
| event_type    | LowCardinality(String)  | ~2 B (dictionary) |
| task_id       | UUID                    | 16 B              |
| user_id       | UUID                    | 16 B              |
| tier          | LowCardinality(String)  | ~2 B (dictionary) |
| mode          | LowCardinality(String)  | ~2 B (dictionary) |
| model_class   | LowCardinality(String)  | ~2 B (dictionary) |
| cost          | UInt32                  | 4 B               |
| status        | LowCardinality(String)  | ~2 B (dictionary) |
| billing_state | LowCardinality(String)  | ~2 B (dictionary) |
| ts            | DateTime64(3)           | 8 B               |
| **Total/row** |                         | **~72 B (raw), ~200 B uncompressed with overhead** |

LowCardinality columns (event_type, tier, mode, model_class, status, billing_state) compress extremely well -- dictionary encoding reduces them to ~2 bytes each regardless of string length.

### Storage growth

| Metric              | Value                 |
| ------------------- | --------------------- |
| Events/day          | ~90M (same as Redpanda topics) |
| Raw row size        | ~200 B                |
| Compression ratio   | ~5-10x (MergeTree)    |
| Compressed/day      | ~2-4 GB               |
| Retention           | 365 days              |
| Steady-state disk   | **~730 GB - 1.5 TB**  |

MergeTree with LowCardinality achieves excellent compression for this schema because most columns have very low cardinality (6 event types, 3 tiers, 2 modes, 3 model classes, 4 statuses, 4 billing states). The UUIDs and timestamps are the primary space consumers.

Provision **1 TB SSD** for ClickHouse (conservative estimate). ClickHouse is not in the critical path -- if it falls behind or runs out of disk, no client-facing behavior is affected.

---

## Infrastructure summary

### Compose deployment (fixed capacity, single host)

| Component        | Instances | CPU / GPU        | Memory      | Disk        |
| ---------------- | --------- | ---------------- | ----------- | ----------- |
| API              | 2         | 2 cores          | 400 MB      | -           |
| OAuth (Hydra)    | 1         | 0.5 cores        | 256 MB      | -           |
| Workers (C=1)    | 1,200     | 1,200 cores      | 120 GB      | -           |
| Outbox relay     | 1         | 0.5 cores        | 128 MB      | -           |
| Dispatcher       | 1         | 0.5 cores        | 128 MB      | -           |
| Projector        | 1         | 0.5 cores        | 128 MB      | -           |
| Reconciler       | 1         | 0.25 cores       | 64 MB       | -           |
| Webhook worker   | 1         | 0.25 cores       | 64 MB       | -           |
| Redis            | 1         | 1 core           | 8 GB        | 512 MB AOF  |
| Postgres         | 1         | 6 cores          | 12 GB       | 4 TB SSD    |
| TigerBeetle      | 1         | 2 cores          | 4 GB        | 3 TB SSD    |
| Redpanda         | 1         | 2 cores          | 4 GB        | 300 GB SSD  |
| RabbitMQ         | 1         | 2 cores          | 4 GB        | 1 GB        |
| Prometheus       | 1         | 1 core           | 2 GB        | 50 GB       |
| Grafana          | 1         | 0.5 cores        | 512 MB      | -           |
| **Total**        | **~15**   | **~1,219 cores** | **~156 GB** | **~7.4 TB** |

With `--profile analytics`:

| Event exporter   | 1         | 0.25 cores       | 128 MB      | -           |
| ClickHouse       | 1         | 2 cores          | 4 GB        | 1 TB SSD    |
| **Total (+analytics)** | **~17** | **~1,221 cores** | **~160 GB** | **~8.4 TB** |

Note: 1,200 workers on a single Compose host is unrealistic. This model shows what the workload demands; production uses orchestration (K8s, ECS) across multiple nodes.

vs Sol 0: 6 additional containers (outbox relay, dispatcher, projector, reconciler, webhook worker, + TigerBeetle + Redpanda). Less PG disk (4 TB vs 4.5 TB provisioned) because billing lives in TigerBeetle. Half the Redis memory (8 GB vs 16 GB). New TigerBeetle instance (4 GB RAM, 3 TB disk). New Redpanda instance (4 GB RAM, 300 GB disk). New RabbitMQ instance (4 GB RAM).

vs Sol 2: Replaces the watchdog with a reconciler. Adds TigerBeetle, Redpanda, and dispatcher containers. Removes `credit_reservations` and `credit_transactions` from PG (saves ~4.1 TB PG disk). Total disk is comparable (~7.4 TB vs ~8 TB) because TigerBeetle and Redpanda absorb the data that left Postgres.

### Production deployment (elastic, multi-node)

| Component      | Min                   | Max   | Scaling signal                 |
| -------------- | --------------------- | ----- | ------------------------------ |
| API            | 2                     | 5     | HTTP p99 latency > 200ms       |
| Workers        | 200                   | 2,800 | RabbitMQ queue depth           |
| Redis          | 1 primary + 1 replica | same  | Memory > 80% -> vertical scale |
| Postgres       | 1 primary + 1 replica | same  | Connection saturation, IOPS    |
| PgBouncer      | 2 (HA)                | same  | Worker connection fan-out      |
| TigerBeetle    | 1 (single-node)       | 3     | Transfer latency p99           |
| Redpanda       | 1                     | 3     | Consumer lag, disk > 80%       |
| RabbitMQ       | 1 (mirrored queues)   | 3     | Queue depth, memory > 70%      |
| OAuth (Hydra)  | 2 (HA)                | same  | Token request latency          |
| Outbox relay   | 1                     | 2     | Outbox lag > 1s                |
| Dispatcher     | 1                     | 2     | Dispatcher lag > 100 events    |
| Projector      | 1                     | 2     | Projection lag > 30s           |
| Reconciler     | 1                     | 1     | N/A (singleton)                |
| Webhook worker | 1                     | 3     | Webhook backlog                |

### Cost drivers (ranked)

1. **Workers** -- 90%+ of compute cost. At peak: 2,332 cores (or GPUs). The dominant cost by far.
2. **TigerBeetle disk** -- 3 TB SSD at year 1, growing ~2.8 TB/year. Append-only ledger with no retention pruning. The largest growing storage cost.
3. **Postgres disk** -- 4 TB SSD at steady-state. ~0.9x Sol 0. Dominated by `cmd.task_commands` (90d, 1.7 TB) + `query.task_query_view` (120d, 1.9 TB).
4. **Redpanda disk** -- 300 GB SSD, bounded by 7-day retention. Modest and stable.
5. **RabbitMQ** -- 4 GB memory, 2 cores. Transient dispatch only. Modest cost.
6. **Redis memory** -- 8 GB. Smallest of all solutions. Stable regardless of customer growth.
7. **ClickHouse disk** (optional) -- 1 TB SSD at year 1. Cheap columnar storage. Not in critical path.
8. **Network** -- ~295 GB/day internal. 2x Sol 0. Negligible cost.
9. **Sidecar services** -- outbox relay, dispatcher, projector, reconciler, webhook worker. 5 lightweight containers (~512 MB combined). Trivial compute cost but each adds a monitoring/alerting surface.
10. **API + OAuth** -- 2-5 instances. Trivial compared to workers.

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

From the flat RFC: `W=8, C=6, U=0.70, T_avg=2.0s, P=1.2`

Note: `C=6` means each worker process can handle 6 concurrent tasks (e.g., via async consumption from RabbitMQ with prefetch=6). This is a compose-scale setting for demo purposes. The production capacity model above uses `C=1` (one task per compute unit) because each task occupies 1 GPU. `T_avg=2.0s` is the compose-scale baseline (small model only); the production model uses `T_avg=2.8s` (weighted model-class mix).

### Example A: compose scale (W=8, C=6, U=0.70, T=2.0s, P=1.2)

- `R_task = (8 x 6 x 0.70) / 2.0 = 16.80 task/sec`
- `R_poll = 1.2 x 16.80 = 20.16 req/sec`
- `M_task = 16.80 x 2,592,000 = 43,545,600 tasks/month`
- `M_poll = 20.16 x 2,592,000 = 52,254,720 polls/month`

### Example B: compose scale doubled (W=16, C=6, same U, T, P)

- `R_task = (16 x 6 x 0.70) / 2.0 = 33.60 task/sec`
- `R_poll = 1.2 x 33.60 = 40.32 req/sec`
- `M_task = 33.60 x 2,592,000 = 87,091,200 tasks/month`
- `M_poll = 40.32 x 2,592,000 = 104,509,440 polls/month`

### Example C: production scale (W=1200, C=1, U=0.85, T=2.8s, P=5)

- `R_task = (1200 x 1 x 0.85) / 2.8 = 364 task/sec`
- `R_poll = 5 x 364 = 1,821 req/sec`
- `M_task = 364 x 2,592,000 = 943,488,000 tasks/month`
- `M_poll = 1,821 x 2,592,000 = 4,720,032,000 polls/month`

### Per-task storage footprint

Postgres: ~400 B (cmd.task_commands) + ~300 B (outbox_events, ephemeral) + ~100 B (inbox_events, ephemeral) + ~350 B (query.task_query_view) = **~1.15 KB/task** (durable, across retention windows).

TigerBeetle: ~128 B (pending transfer) + ~128 B (post/void transfer) = **~256 B/task** (permanent, append-only).

Redis: ~200 B/task active (task:{task_id} hash within 24h TTL window).

Redpanda: ~1 KB/task (~2 events x 500 B, retained for 7 days).

vs Sol 0: ~1.25 KB/task PG + ~1.1 KB/task Redis = ~2.35 KB total. Sol 3 is ~1.61 KB total (PG + TB + Redis). The PG footprint per task is smaller than Sol 2 (no credit_reservations or credit_transactions rows), but TigerBeetle adds permanent 256 B/task. The overall per-task cost is cheaper in RAM (Redis) and more expensive in long-term disk (TigerBeetle ledger never prunes).

### Sensitivity notes

Most sensitive variables:

- `T_avg` (model mix shifts quickly change throughput and queue depth -- switching from 60/30/10 to 40/40/20 raises T_avg from 2.8s to 3.2s, a 14% throughput reduction)
- Worker count `W`
- Utilization `U` under real incident/retry behavior
- Cold-start rate (if hot/cold routing degrades, effective T_avg increases toward 3.0s + 3s load = worse)
- TigerBeetle disk growth (no pruning -- must plan for multi-year ledger)
- Outbox relay throughput (if relay falls behind, outbox table grows unbounded)
- Redpanda consumer lag (if projector or dispatcher falls behind, rebuild time increases)

Infrastructure bottleneck analysis:

- **TigerBeetle:** ~1M transfers/sec capacity vs ~2K/sec demand. **Not a bottleneck.** At 0.2% utilization.
- **Redpanda:** ~100K msg/sec capacity vs ~2K/sec demand. **Not a bottleneck.** At ~2% utilization.
- **RabbitMQ:** ~50K msg/sec capacity vs ~1K/sec demand. **Not a bottleneck.** Hot/cold routing adds negligible overhead.
- **Postgres:** Write path is the constraint: 3 tables per submit txn (cmd + outbox + concurrency check). At ~1,040 submits/sec peak, PG handles ~3,120 writes/sec. Well within capability but monitor WAL throughput.
- **Redis:** ~100K ops/sec capacity vs ~10K/sec demand. **Not a bottleneck.**
- **Workers:** The bottleneck. Always the bottleneck. 90%+ of cost and the sole constraint on throughput.

Operational recommendation:

- Monitor outbox lag as a primary health signal. If `outbox_unpublished_count` grows, the system is falling behind.
- Monitor Redpanda consumer lag per consumer group (dispatcher, projector, webhook, analytics). Each can lag independently.
- RabbitMQ queue depth (per hot/cold queue) is the primary autoscaling signal -- prefer it over CPU-based scaling.
- TigerBeetle disk usage requires long-term capacity planning (no retention pruning).
- Recompute this model using measured output from monitoring before any production commitment.
