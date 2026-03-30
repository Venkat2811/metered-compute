# RFC-0000: Request Flow Diagrams

Parent: [RFC-0000 README](./README.md)

Every diagram below shows the exact sequence of store calls for one API request. The "DB calls on happy path" count directly answers the assignment question: _"how can we reduce the number of calls?"_

---

## 1. Auth middleware (every authenticated request)

```text
Client                 API                      Redis                    Postgres
  |                     |                         |                         |
  |-- Bearer <api_key>->|                         |                         |
  |                     |-- HGETALL auth:{key} -->|                         |
  |                     |<----- hash / nil -------|                         |
  |                     |                         |                         |
  |                     | [cache hit] return AuthUser                       |
  |<----- AuthUser -----|                         |                         |
  |                     |                         |                         |
  |                     | [cache miss]                                      |
  |                     |-- SELECT * FROM users --|-------- WHERE k=$1 ---->|
  |                     |<------------------------|--------- user row ------|
  |                     |-- HSET auth:{key} ----->|                         |
  |                     |-- EXPIRE auth:{key} --->|                         |
  |                     |-- SETNX credits:{uid} ->|                         |
  |<----- AuthUser -----|                         |                         |
```

**DB calls:** 0 on cache hit (typical), 1 on cache miss (first request or after TTL expiry).

Cache stores `user_id, name, role` only. Credits are cached separately via `SETNX` to avoid stale balance reads. Auth cache is invalidated when admin updates credits.

---

## 2. Submit path (`POST /v1/task`)

```text
Client                 API                      Redis                    Postgres
  |                     |                         |                         |
  |-- POST /v1/task --->|                         |                         |
  |   {x:5, y:3}       |                         |                         |
  |                     | [auth middleware: 0 DB calls on cache hit]        |
  |                     |                         |                         |
  |                     |== EVALSHA admission ===>|                         |
  |                     |   - check idem / active / credits                 |
  |                     |   - deduct credits, set idem, incr active         |
  |                     |<======= {ok:true} ======|                         |
  |                     |-- HSET pending:{tid} -->|                         |
  |                     |-- EXPIRE pending 120s ->|                         |
  |                     |-- BEGIN; INSERT tasks --|--- + credit_txn; COMMIT>|
  |                     |-- DEL pending:{tid} --->|                         |
  |                     |-- celery.send_task() -->|                         |
  |<-- 201 {task_id} ---|                         |                         |
```

**DB calls on happy path: 1** (single PG transaction for task row + credit audit). All admission logic (idempotency, concurrency, credit check, deduction) happens in Redis via one Lua EVALSHA. Without Redis: this would be 4+ PG queries with row-level locking.

**On Lua CACHE_MISS** (credits key missing in Redis):

```text
API                      Redis                    Postgres
  |                       |                         |
  |== EVALSHA admission =>|                         |
  |<= {reason:CACHE_MISS}=|                         |
  |-- SELECT credits FROM users WHERE api_key=$1 -->|
  |<-------------------------- credits=500 ---------|
  |-- SET credits:{uid} ->|                         |
  |== EVALSHA retry =====>|                         |
  |<= {ok:true} ==========|                         |
```

Adds 1 DB call for hydration, then retries Lua. Total: 2 DB calls (hydration + persist).

**On PG persist failure** (compensation):

```text
Client                 API                      Redis                    Postgres
  |                     |                         |                         |
  |                     |-- INSERT tasks + credit_txn -------------------->|
  |                     |<------------------------------ PG error ---------|
  |                     |-- INCRBY credits refund>|                         |
  |                     |-- DECR_ACTIVE_CLAMP --->|                         |
  |                     |-- DEL idem:{uid}:{key}->|                         |
  |                     |-- DEL pending:{tid} --->|                         |
  |<------- 503 --------|                         |                         |
```

---

## 3. Poll path (`GET /v1/poll`)

```text
Client                 API                      Redis                    Postgres
  |                     |                         |                         |
  |-- GET /v1/poll ---->|                         |                         |
  |   ?task_id=X        |                         |                         |
  |                     | [auth middleware: 0 DB calls on cache hit]        |
  |                     |-- HGETALL result:{tid}->|                         |
  |                     |<------- row / nil ------|                         |
  |                     |                         |                         |
  |                     | [cache hit] validate user_id and return 200       |
  |<----- 200 ----------|                         |                         |
  |                     |                         |                         |
  |                     | [cache miss]                                      |
  |                     |-- SELECT * FROM tasks --|-- WHERE task_id=$1 ---->|
  |                     |<------------------------|------- task row --------|
  |<----- 200 ----------|                         |                         |
```

**DB calls:** 0 if result cached in Redis (completed tasks), 1 on cache miss (pending/running tasks, or expired cache).

For completed tasks, the worker populates `result:{task_id}` in Redis with a 24h TTL. Subsequent polls serve from Redis until expiry.

---

## 4. Cancel path (`POST /v1/task/{id}/cancel`)

```text
Client                 API                      Redis                    Postgres
  |                     |                         |                         |
  |-- POST cancel ----->|                         |                         |
  |                     | [auth middleware: 0 DB calls on cache hit]        |
  |                     |-- BEGIN; UPDATE tasks --|-- SET status=CANCELLED->|
  |                     |   WHERE task_id=$1 AND status IN(PENDING,RUNNING) |
  |                     |<------------------------|---- rows_affected ------|
  |                     |                         |                         |
  |                     | [rows=0] ROLLBACK -> 409                          |
  |<----- 409 ----------|                         |                         |
  |                     |                         |                         |
  |                     | [rows=1] INSERT credit_txn(+cost); COMMIT ------->|
  |                     |-- INCRBY credits cost ->|                         |
  |                     |-- SADD credits:dirty -->|                         |
  |                     |-- DECR_ACTIVE_CLAMP --->|                         |
  |                     |-- celery.control.revoke(task_id) (best effort)    |
  |<----- 200 ----------|                         |                         |
```

**DB calls: 1** (single PG transaction for guarded status update + credit audit). Redis refund + counter decrement are non-blocking follow-ups.

The guarded `WHERE status IN ('PENDING', 'RUNNING')` prevents cancel from overwriting a terminal state that the worker already wrote. If the worker completes between the client's request and the UPDATE, the UPDATE affects 0 rows -> 409 Conflict.

---

## 5. Worker execution (Celery task)

```text
Celery Queue           Worker                   Redis                    Postgres
  |                     |                         |                         |
  |-- deliver msg ----->|                         |                         |
  |                     |-- UPDATE tasks SET st --|-- atus='RUNNING' ------>|
  |                     |   WHERE task_id=$1 AND status='PENDING'           |
  |                     |<------------------------|---- rows_affected ------|
  |                     |                                                   |
  |                     | [rows=0] already terminal -> ACK                  |
  |<-------- ACK -------|                         |                         |
  |                     |                                                   |
  |                     | [rows=1] model(x,y) -> result (~2s)              |
  |                     |-- UPDATE tasks SET st --|-- atus='COMPLETED' ---->|
  |                     |   result=$2, runtime_ms=$3 WHERE status='RUNNING' |
  |                     |-- HSET result:{tid} --->|                         |
  |                     |-- EXPIRE result 24h --->|                         |
  |                     |-- DECR_ACTIVE_CLAMP --->|                         |
  |                     |-- SADD credits:dirty -->|                         |
```

**DB calls: 2** (PENDING->RUNNING transition + RUNNING->COMPLETED transition). Both are guarded UPDATEs that prevent race conditions.

**On worker failure:**

```text
Worker                   Redis                    Postgres
  |                       |                         |
  |  [exception during model()]                     |
  |-- BEGIN; UPDATE tasks>|--- -> FAILED; --------->|
  |   INSERT credit_txn(+cost); COMMIT              |
  |-- INCRBY credits ---->|                         |
  |-- DECR_ACTIVE_CLAMP ->|                         |
  |-- SADD credits:dirty->|                         |
```

---

## 6. Admin credits (`POST /v1/admin/credits`)

```text
Client                 API                      Redis                    Postgres
  |                     |                         |                         |
  |-- POST credits ---->|                         |                         |
  |   {api_key,delta}   |                         |                         |
  |                     | [auth middleware: admin role required]             |
  |                     |-- CTE: UPDATE users + --|-- INSERT credit_txn --->|
  |                     |<------------------------|-- user_id,new_bal ------|
  |                     |-- SET credits:{uid} --->|                         |
  |                     |-- SADD credits:dirty -->|                         |
  |                     |-- DEL auth:{api_key} -->|                         |
  |<----- 200 ----------|                         |                         |
```

**DB calls: 1** (single CTE atomically updates balance + inserts audit). Redis sync is a follow-up, not blocking.

---

## 7. Reaper cycle (every 30s, background)

```text
[Reaper tick every 30s]
      |
      +--> (1) Orphan marker recovery
      |         - SCAN pending:*
      |         - if marker age > 60s:
      |             SELECT task by task_id (Postgres)
      |             * exists  -> DEL marker (Redis)
      |             * missing -> INCRBY refund + DECR_ACTIVE_CLAMP
      |                         DEL idem key + DEL marker (Redis)
      |
      +--> (2) Stuck task recovery
      |         - SELECT RUNNING tasks older than 5m (Postgres)
      |         - for each stuck task:
      |             UPDATE status='FAILED' + INSERT credit_txn (Postgres)
      |             INCRBY refund + DECR active (Redis)
      |
      +--> (3) Credit snapshot flush
      |         - SMEMBERS credits:dirty (Redis)
      |         - for each user_id:
      |             GET credits:{uid} (Redis)
      |             UPSERT credit_snapshots (Postgres)
      |             SREM credits:dirty (Redis)
      |
      `--> (4) Result expiry
                - UPDATE tasks SET status='EXPIRED'
                  WHERE completed_at < now() - 24h (Postgres)
```

---

## Credit lifecycle (end-to-end)

Shows how a credit balance moves through the system across a full task lifecycle:

```text
(0) Initial state
    - Postgres users.credits = 500
    - Redis credits:U is missing
    - Postgres credit_txn is empty
            |
            v
(1) First request (auth cache miss)
    - SETNX credits:U 500
    - Redis now has the working balance
            |
            v
(2) Submit task (cost=10)
    - Redis:  DECRBY credits:U 10 -> 490
    - Postgres: INSERT credit_txn(delta=-10)
    - Note: users.credits may still be 500 (stale)
            |
            v
(3) Reaper flush (~30s)
    - GET credits:U from Redis
    - UPSERT credit_snapshots(U,490) in Postgres
    - Remove user from credits:dirty
            |
            v
(4) Redis restart and rehydrate
    - Lua returns CACHE_MISS
    - API loads snapshot 490 from Postgres
    - Redis SET credits:U 490

Risk:
- If Redis crashes before reaper flush, snapshot can lag and restore stale balance.
- Mitigation: Redis AOF (appendfsync everysec) reduces the loss window.
- Safety: under-charge is possible; over-charge is not.
```
