# RFC-0001: Request Flow Diagrams

Parent: [RFC-0001 README](./README.md)

Every diagram below shows the exact sequence of store calls for one API request. The "DB calls on happy path" count directly answers the assignment question: _"how can we reduce the number of calls?"_

---

## 1. OAuth token acquisition (`POST /v1/oauth/token`)

```text
Client                 API                    Hydra                  PG (api_keys)
  |                      |                      |                      |
  |-- POST token ------->|                      |                      |
  |  {api_key:"..."}     |                      |                      |
  |                      |                      |                      |
  |                      |-- SELECT EXISTS ----->|----- hash+query ---->|
  |                      |   api_keys.key_hash  |   =sha256()          |
  |                      |<--- true ------------|<--- exists ----------|
  |                      |                      |                      |
  |                      |-- POST /oauth2/token>|                      |
  |                      |   client_credentials |                      |
  |                      |<-- {access_token} ---|                      |
  |                      |                      |                      |
  |<-- 200 {jwt} --------|                      |                      |
```

**DB calls: 1** (api_key hash validation against PG). This is the ONLY path that touches PG for auth. All subsequent requests use JWT local verification with zero network calls.

---

## 2. JWT auth middleware (every authenticated request)

```text
Client                      API                     Redis
  |                          |                         |
  |-- Authorization:         |                         |
  |   Bearer <jwt> --------->|                         |
  |                          |                         |
  |                    [JWT verify (local crypto)]     |
  |                    decode + verify RS256           |
  |                    check exp, iss claims           |
  |                          |                         |
  |                    [revocation check]              |
  |                          |-- pipeline (1 RTT):     |
  |                          |   SISMEMBER             |
  |                          |     revoked:{uid}:today |
  |                          |   SISMEMBER             |
  |                          |     revoked:{uid}:yday  |
  |                          |   --------------------> |
  |                          |<-- [false, false] ------|
  |                          |                         |
  |                    [derive AuthUser from claims]   |
  |<--- AuthUser ------------|                         |
```

**DB calls: 0** (when Redis is up). Auth is local JWT crypto + 1 Redis RTT (pipelined) for day-sharded revocation check. Only the JTI is checked (not the full token) — JWT cryptographic verification makes the full token unnecessary for blacklist lookup. If Redis is unavailable, revocation falls back to Postgres `token_revocations` table (1 DB call). vs solution 0: same 0 DB calls on cache hit, but solution 1 never has a cache miss because JWT claims carry identity directly.

---

## 3. Submit path (`POST /v1/task`)

```text
Client                 API                    Redis                  Postgres
  |                      |                      |                      |
  |-- POST /v1/task ---->|                      |                      |
  |  {x:5, y:3}         |                      |                      |
  |                      |                      |                      |
  |              [JWT auth: 0 DB calls, 1 Redis RTT for revocation]    |
  |                      |                      |                      |
  |                      |== EVALSHA Lua ======>|                      |
  |                      |   (1 RTT, 10 ops)    |                      |
  |                      |   1. GET idem:{uid}  |                      |
  |                      |   2. GET active:{uid}|                      |
  |                      |   3. GET credits:{u} |                      |
  |                      |   4. DECRBY credits  |                      |
  |                      |   5. XADD stream     |  <- enqueue in Lua   |
  |                      |   6. HSET task:{tid} |  <- task state       |
  |                      |   7. EXPIRE task:{}  |                      |
  |                      |   8. SETEX idem key  |                      |
  |                      |   9. INCR active     |                      |
  |                      |  10. SADD dirty      |                      |
  |                      |<== {ok:true} ========|                      |
  |                      |                      |                      |
  |                      |-- HSET pending:{tid}>|  (recovery marker)   |
  |                      |-- EXPIRE (120s) ---->|  (pipeline, 1 RTT)   |
  |                      |                      |                      |
  |                      |-- BEGIN txn -------->|---INSERT tasks ----->|
  |                      |                      |   INSERT credit_txn  |
  |                      |                      |<---- COMMIT ---------|
  |                      |                      |                      |
  |                      |-- DEL pending:{tid}->|                      |
  |                      |                      |                      |
  |<-- 201 {task_id} ----|                      |                      |
```

**DB calls on happy path: 1** (single PG transaction for task row + credit audit). The entire admission gate — idempotency, concurrency, credit check, deduction, stream enqueue, and task state write — happens in 1 Redis Lua EVALSHA. vs solution 0: same 1 PG call, but solution 0 has a separate Celery publish step after PG persist; here, enqueue is inside the Lua script.

### On Lua CACHE_MISS (credits key missing in Redis)

```text
API                    Redis                  Postgres
  |                      |                      |
  |== EVALSHA Lua ======>|                      |
  |<== {CACHE_MISS} =====|                      |
  |                      |                      |
  |-- SELECT credits --->|------- query ------->|
  |   FROM users         |                      |
  |<-- credits=500 ------|<------ row ----------|
  |                      |                      |
  |-- SET credits:U 500->|                      |
  |                      |                      |
  |== EVALSHA (retry) ==>|  (now succeeds)      |
```

Adds 1 DB call for hydration, then retries Lua. Total: 2 DB calls (hydration + persist).

### On PG persist failure (compensation)

```text
Client                 API                    Redis                  Postgres
  |                      |                      |<---- PG error -------|
  |                      |                      |                      |
  |                      |-- INCRBY credits --->|  (undo deduction)    |
  |                      |-- DECR_ACTIVE Lua -->|  (undo concurrency)  |
  |                      |-- DEL idem key ----->|  (undo idempotency)  |
  |                      |-- DEL pending ------>|  (cleanup)           |
  |                      |                      |                      |
  |<-- 503 --------------|                      |                      |
```

Note: unlike solution 0, the stream entry (XADD) was already written inside the Lua script. The worker will pick it up but find no PG task row — the worker returns it to the PEL (no XACK), and the reaper eventually cleans it up via orphan marker recovery.

---

## 4. Poll path (`GET /v1/poll`)

```text
Client                 API                    Redis                  Postgres
  |                      |                      |                      |
  |-- GET /v1/poll ----->|                      |                      |
  |   ?task_id=X         |                      |                      |
  |                      |                      |                      |
  |              [JWT auth: 0 DB calls]         |                      |
  |                      |                      |                      |
  |              TIER 1: completed result cache |                      |
  |                      |-- HGETALL result:t ->|                      |
  |                      |                      |                      |
  |              [hit?] YES: check user_id match|                      |
  |                      |<-- {status,result} --|                      |
  |<-- 200 response -----|                      |                      |
  |                      |                      |                      |
  |              [miss] TIER 2: task state hash |                      |
  |                      |-- HGETALL task:t --->|                      |
  |                      |                      |                      |
  |              [hit?] YES: check user_id match|                      |
  |                      |<-- {status:PENDING} -|                      |
  |                      |-- XLEN stream ------>|  (queue depth est.)  |
  |<-- 200 response -----|                      |                      |
  |                      |                      |                      |
  |              [miss] TIER 3: PG fallback     |                      |
  |                      |-- SELECT tasks ----->|------- query ------->|
  |                      |<-- task row ---------|<------ row ----------|
  |<-- 200 response -----|                      |                      |
```

**DB calls: 0** on happy path (both pending AND completed tasks served from Redis). The Lua mega-script writes `task:{task_id}` at submit time, so even PENDING tasks have a Redis hash immediately. PG fallback only when both Redis hashes are missing (expired or Redis restart). **This is the key improvement vs solution 0**, where pending tasks always required a PG SELECT.

---

## 5. Cancel path (`POST /v1/task/{id}/cancel`)

```text
Client                 API                    Redis                  Postgres
  |                      |                      |                      |
  |-- POST cancel ------>|                      |                      |
  |                      |                      |                      |
  |              [JWT auth: 0 DB calls]         |                      |
  |                      |                      |                      |
  |                      |-- SELECT task ------>|------- query ------->|
  |                      |<-- task row ---------|<------ row ----------|
  |                      |   (check owner)      |                      |
  |                      |                      |                      |
  |                      |-- BEGIN txn -------->|---UPDATE tasks ----->|
  |                      |   SET CANCELLED      |   WHERE status IN    |
  |                      |   WHERE status IN    |   (PENDING,RUNNING)  |
  |                      |   (PENDING,RUNNING)  |                      |
  |                      |   INSERT credit_txn->|---delta=+cost ------>|
  |                      |                      |<---- COMMIT ---------|
  |                      |                      |                      |
  |                      |-- INCRBY credits --->|  (refund)            |
  |                      |-- SADD dirty ------->|                      |
  |                      |-- DECR_ACTIVE Lua -->|  (decrement)         |
  |                      |-- HSET task CANCEL ->|  (update Redis)      |
  |                      |-- DEL pending:{tid}->|  (cleanup marker)    |
  |                      |                      |                      |
  |<-- 200 --------------|                      |                      |
```

**DB calls: 2** (SELECT task for ownership check + transaction for guarded cancel + credit audit). Redis refund, state update, and marker cleanup are non-blocking follow-ups. Cancel also updates the `task:{task_id}` hash so subsequent polls see CANCELLED from Redis immediately.

The guarded `WHERE status IN ('PENDING', 'RUNNING')` prevents cancel from overwriting a terminal state that the worker already wrote. If the worker completes between the client's request and the UPDATE, the UPDATE affects 0 rows → 409 Conflict.

---

## 6. Worker execution (Redis Streams)

```text
Redis Stream           Worker                 Redis                  Postgres
  |                      |                      |                      |
  |-- XREADGROUP ------->|  (or XAUTOCLAIM)     |                      |
  |   tasks:stream       |                      |                      |
  |   count=1            |                      |                      |
  |<- [{msg_id,fields}]--|                      |                      |
  |                      |                      |                      |
  |                      |-- UPDATE tasks ----->|------- query ------->|
  |                      |   SET status=RUNNING |   WHERE task_id=$1   |
  |                      |   WHERE PENDING      |   AND status=PENDING |
  |                      |                      |                      |
  |                      |  [rows_affected==0?] |                      |
  |                      |  [task row missing?] |                      |
  |                      |    no XACK, retry    |                      |
  |                      |  [already terminal?] |                      |
  |                      |    XACK, skip        |                      |
  |                      |                      |                      |
  |                      |  [rows_affected==1:] |                      |
  |                      |-- HSET task:RUNNING->|                      |
  |                      |   model(x,y)->result |  (~2-10s sleep)      |
  |                      |                      |                      |
  |                      |-- UPDATE tasks ----->|------- query ------->|
  |                      |   SET COMPLETED      |   result=$2          |
  |                      |   WHERE RUNNING      |   runtime=$3         |
  |                      |                      |<---- OK -------------|
  |                      |                      |                      |
  |                      |-- HSET result:{tid}->|  (result cache)      |
  |                      |-- HSET task:DONE --->|  (task state)        |
  |                      |-- DECR_ACTIVE Lua -->|  (decrement active)  |
  |                      |-- XACK msg_id ------>|                      |
  |                      |                      |                      |
  |                      |-- UPSERT checkpoint->|------- query ------->|
```

**DB calls: 3** (PENDING→RUNNING transition + RUNNING→COMPLETED transition + stream checkpoint persist). Both state transitions are guarded UPDATEs. Worker writes both `result:{task_id}` (rich cache for poll) and updates `task:{task_id}` hash.

### On worker failure

```text
Worker                 Redis                  Postgres
  |                      |                      |
  |  [exception during model()]                 |
  |                      |                      |
  |-- BEGIN txn -------->|---UPDATE tasks ----->|
  |   SET status=FAILED  |   WHERE status IN    |
  |                      |   (PENDING,RUNNING)  |
  |   INSERT credit_txn->|---delta=+cost ------>|
  |                      |<---- COMMIT ---------|
  |                      |                      |
  |-- INCRBY credits --->|  (refund)            |
  |-- DECR_ACTIVE Lua -->|                      |
  |-- HSET result FAIL ->|  (cache failure)     |
  |-- HSET task FAILED ->|  (task state)        |
  |-- XACK msg_id ------>|                      |
```

---

## 7. PEL recovery (XAUTOCLAIM — inline in worker loop)

```text
Worker                               Redis
  |                                     |
  |== XAUTOCLAIM =======================|
  |-- XAUTOCLAIM tasks:stream           |
  |   group="workers"                   |
  |   consumer=self                     |
  |   min_idle_time=15000 (15s)         |
  |   start_id=cursor ----------------->|
  |                                     |
  |<-- [next_cursor, claimed_entries] --|
  |                                     |
  |  for each claimed entry:            |
  |    -> same processing as new message|
  |    -> task may be CANCELLED/FAILED  |
  |       already -> just XACK          |
```

PEL recovery runs **inline within each worker's main loop** (not a separate process). Workers prioritize claimed idle entries over new messages. Uses `XAUTOCLAIM` (modern Redis 6.2+) instead of `XCLAIM` for simpler cursor-based recovery without separate `XPENDING` calls.

---

## 8. Admin credits (`POST /v1/admin/credits`)

```text
Client                 API                    Redis                  Postgres
  |                      |                      |                      |
  |-- POST credits ----->|                      |                      |
  |  {api_key, delta,    |                      |                      |
  |   reason}            |                      |                      |
  |                      |                      |                      |
  |              [JWT auth: verify admin role + admin_credits scope]   |
  |                      |                      |                      |
  |                      |-- CTE: UPDATE usr -->|------- query ------->|
  |                      |   credits=credits+$1 |   + INSERT credit_txn|
  |                      |   RETURNING credits  |                      |
  |                      |<-- (uid, new_bal) ---|<------ row ----------|
  |                      |                      |                      |
  |                      |-- SET credits:{uid}->|  (sync Redis bal.)   |
  |                      |-- SADD dirty ------->|  (mark for snapshot) |
  |                      |-- DEL auth:{key} --->|  (invalidate cache)  |
  |                      |                      |                      |
  |<-- 200 --------------|                      |                      |
```

**DB calls: 1** (single CTE atomically updates balance + inserts audit). Redis sync is a follow-up, not blocking.

---

## 9. Token revocation (`POST /v1/auth/revoke`)

```text
Client                 API                    Redis                  Postgres
  |                      |                      |                      |
  |-- POST revoke ------>|                      |                      |
  |  (Bearer JWT)        |                      |                      |
  |                      |                      |                      |
  |              [JWT auth: extract jti + exp from verified claims]    |
  |                      |                      |                      |
  |                      |-- SADD revoked:day ->|                      |
  |                      |   member=jti         |                      |
  |                      |-- EXPIRE (TTL) ----->|                      |
  |                      |                      |                      |
  |                      |-- INSERT revocations>|------- query ------->|
  |                      |   (jti, uid, exp_at) |                      |
  |                      |                      |<---- OK -------------|
  |                      |                      |                      |
  |<-- 200 --------------|                      |                      |
```

**DB calls: 1** (INSERT into day-partitioned `token_revocations`). Redis write is the hot-cache path (immediate revocation). Postgres is the durable record. Only the JTI is stored (not the full token) — cryptographic JWT verification makes the JTI sufficient for blacklist identification (~36 bytes vs ~800 bytes per revocation).

---

## 10. Reaper cycle (every ~30s, background)

```text
Reaper                 Redis                  Postgres
  |                      |                      |
  |== 1. Orphan marker recovery ===============|
  |-- SCAN pending:* --->|                      |
  |   for each >60s:     |                      |
  |-- SELECT task ------>|------- query ------->|
  |   [exists?] DEL mkr  |                      |
  |   [missing?] refund: |                      |
  |-- INCRBY credits --->|                      |
  |-- DECR_ACTIVE Lua -->|                      |
  |-- DEL idem, marker ->|                      |
  |                      |                      |
  |== 2. Stuck task recovery ===================|
  |-- SELECT tasks ----->|------- query ------->|
  |   WHERE RUNNING      |   started_at < cutoff|
  |   for each stuck:    |                      |
  |-- BEGIN txn: FAILED->|------- query ------->|
  |-- INSERT credit_txn->|------- query ------->|
  |-- INCRBY credits --->|                      |
  |-- DECR_ACTIVE Lua -->|                      |
  |                      |                      |
  |== 3. Credit snapshot flush =================|
  |-- SMEMBERS dirty --->|                      |
  |   for each dirty:    |                      |
  |-- GET credits:{uid}->|                      |
  |-- UPSERT snapshots ->|------- query ------->|
  |-- SREM dirty ------->|                      |
  |                      |                      |
  |== 4. Credit drift audit ====================|
  |-- SELECT snapshots ->|------- query ------->|
  |   for each user:     |                      |
  |-- GET credits:{uid}->|                      |
  |   [drift != 0?]      |                      |
  |-- UPSERT snapshot -->|------- query ------->|
  |-- INSERT drift_audit>|------- query ------->|
  |                      |                      |
  |== 5. Result expiry =========================|
  |-- UPDATE tasks ----->|------- query ------->|
  |   completed < cutoff |                      |
```

Revocation partition lifecycle (`token_revocations` day-partitions: create future, drop expired) is handled by `pg_partman` inside Postgres — not by the reaper. This keeps DDL concerns at the database level.

vs solution 0 reaper: this solution adds **phase 4 (credit drift audit)** with a dedicated `credit_drift_audit` table. The drift audit compares Redis balances against PG snapshots, reconciles mismatches, and logs every observation for operational visibility.

---

## Credit lifecycle (end-to-end)

Shows how a credit balance moves through the system across a full task lifecycle:

```text
 INITIAL STATE
 +------------------+  +------------------+  +------------------+
 | PG users         |  | Redis            |  | PG credit_txn    |
 | credits=500      |  | credits:U = ?    |  | (empty)          |
 +------------------+  +------------------+  +------------------+

 1. FIRST SUBMIT CACHE_MISS (hydrates Redis credits from PG)
    Lua returns CACHE_MISS -> API reads PG snapshot/users -> Redis SET credits:U 500
 +------------------+  +------------------+  +------------------+
 | PG users         |  | Redis            |  | PG credit_txn    |
 | credits=500      |  | credits:U = 500  |  | (empty)          |
 +------------------+  +------------------+  +------------------+

 2. SUBMIT TASK (Lua atomically deducts + enqueues + writes task state)
    Redis DECRBY 10, XADD stream, HSET task:{id}, PG INSERT credit_txn
 +------------------+  +------------------+  +------------------+
 | PG users         |  | Redis            |  | PG credit_txn    |
 | credits=500      |  | credits:U = 490  |  | delta=-10        |
 | (stale!)         |  | dirty={U}        |  | reason=task_dedu |
 +------------------+  | task:{id}=PEND   |  +------------------+
                       +------------------+
    PG users.credits is now STALE -- Redis is ahead

 3. REAPER FLUSHES (every ~30s, writes Redis balance to snapshot)
    Redis GET credits:U -> PG UPSERT credit_snapshots(U, 490)
 +------------------+  +------------------+  +------------------+
 | PG users         |  | Redis            |  | PG snapshots     |
 | credits=500      |  | credits:U = 490  |  | U: balance=490   |
 | (still stale)    |  | dirty={}         |  |                  |
 +------------------+  +------------------+  +------------------+

 4. REDIS RESTARTS (data lost, rehydrated on next CACHE_MISS submit path)
    Lua CACHE_MISS -> PG SELECT snapshots -> Redis SET credits:U 490
 +------------------+  +------------------+
 | PG snapshots     |  | Redis            |
 | U: bal=490       |  | credits:U = 490  |  <- recovered
 +------------------+  +------------------+

 RISK: If Redis dies BEFORE reaper flushes after a deduction,
 the snapshot is stale and the user gets phantom credits back.
 Mitigation: Redis AOF (appendfsync everysec) limits loss to ~1s.
 Safety: under-charge is acceptable; over-charge never happens.
```
