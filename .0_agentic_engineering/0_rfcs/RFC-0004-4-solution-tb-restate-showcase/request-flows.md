# RFC-0004: Request Flow Diagrams

Parent: [RFC-0004 README](./README.md)

Every diagram below shows the exact sequence of store calls for one API request. The "DB calls on happy path" count directly answers the core design question: _"how can we reduce the number of calls?"_

Column convention: Client, API, TigerBeetle (TB), Postgres (PG), Redis, Restate, Compute. Arrows terminate at the system that owns the operation.

---

## 1. Auth (every authenticated request)

```text
Client                  API                     Redis               PG (api_keys + users)
  |                       |                       |                       |
  |-- Authorization:      |                       |                       |
  |   Bearer <api-key> -->|                       |                       |
  |                       |                       |                       |
  |                 [SHA-256 hash key]             |                       |
  |                       |                       |                       |
  |                       |-- GET auth:{hash} --->|                       |
  |                       |<-- {user_id, name} ---|  (cache hit)          |
  |                       |                       |                       |
  |                       |  OR (cache miss):     |                       |
  |                       |-- SELECT user ------->|---------- JOIN ------>|
  |                       |<-- {user_id, name} ---|<--- row -------------|
  |                       |-- SET auth:{hash} --->|  (cache backfill)     |
  |                       |                       |                       |
```

**DB calls: 0** (cache hit) or **1** (cache miss, backfilled for next request). Auth cache TTL: 300s.

---

## 2. Submit (`POST /v1/task`)

```text
Client          API              TigerBeetle         PG (tasks)          Redis            Restate
  |               |                    |                   |                 |                  |
  |-- POST ------>|                    |                   |                 |                  |
  | {x, y}       |                    |                   |                 |                  |
  |               |                    |                   |                 |                  |
  |         [auth: 0-1 DB calls]      |                   |                 |                  |
  |               |                    |                   |                 |                  |
  |               |-- pending_transfer |                   |                 |                  |
  |               |   (user → escrow)  |                   |                 |                  |
  |               |   amount=10,       |                   |                 |                  |
  |               |   timeout=300s --->|                   |                 |                  |
  |               |<-- ok (or         |                   |                 |                  |
  |               |    EXCEEDS_CREDITS)|                   |                 |                  |
  |               |                    |                   |                 |                  |
  |               |  [if EXCEEDS_CREDITS → 402]            |                 |                  |
  |               |                    |                   |                 |                  |
  |               |-- INSERT task ------------------->|                 |                  |
  |               |   (PENDING, tb_transfer_id)       |                 |                  |
  |               |<-- RETURNING * -------------------|                 |                  |
  |               |                    |                   |                 |                  |
  |               |  [if PG fails → void TB transfer, return 500]          |                  |
  |               |                    |                   |                 |                  |
  |               |-- HSET task:{id} -------------------------------->|                  |
  |               |                    |                   |                 |                  |
  |               |-- POST /TaskService/execute_task/send ----------------------->|
  |               |   (fire-and-forget, idempotency-key=task_id)              |
  |               |                    |                   |                 |                  |
  |<-- 201 -------|                    |                   |                 |                  |
  | {task_id,     |                    |                   |                 |                  |
  |  status:      |                    |                   |                 |                  |
  |  PENDING}     |                    |                   |                 |                  |
```

**DB calls: 1 PG** (INSERT task) + **1 TB** (pending transfer) + **1 Redis** (cache write) + **1 Restate** (async invoke).

Compensation: if the PG INSERT fails after the TB pending transfer succeeds, the API immediately voids the TB transfer. This is the only compensation path in the codebase.

---

## 3. Task workflow (Restate durable handler)

```text
Restate              API (/restate handler)     PG (tasks)          TigerBeetle         Redis           Compute
  |                       |                        |                     |                  |                |
  |-- execute_task ------>|                        |                     |                  |                |
  |   {task_id,           |                        |                     |                  |                |
  |    tb_transfer_id,    |                        |                     |                  |                |
  |    x, y}              |                        |                     |                  |                |
  |                       |                        |                     |                  |                |
  |                 [Step 1: mark running - control plane]               |                  |                |
  |                       |-- UPDATE status ------>|                     |                  |                |
  |                       |   = 'RUNNING'          |                     |                  |                |
  |                       |                        |                     |                  |                |
  |                 [Step 2: compute - external data plane]              |                  |                |
  |                       |-- ctx.run("compute")   |                     |                  |                |
  |                       |--------------------------------------------------------------->|                |
  |                       |   POST /compute {x, y, task_id}                               |                |
  |                       |<---------------------------------------------------------------|                |
  |                       |   {sum, product}      |                     |                  |                |
  |                       |                        |                     |                  |                |
  |                 [Step 3: capture credits - control plane (journaled)]|                  |                |
  |                       |-- ctx.run("capture")   |                     |                  |                |
  |                       |   post_pending_transfer --------------------->|                  |                |
  |                       |   (escrow → revenue)   |                     |                  |                |
  |                       |<---------------------------------------------|                  |                |
  |                       |                        |                     |                  |                |
  |                 [Step 4: store result - control plane]               |                  |                |
  |                       |-- UPDATE status ------>|                     |                  |                |
  |                       |   = 'COMPLETED',       |                     |                  |                |
  |                       |   result = {...}       |                     |                  |                |
  |                       |                        |                     |                  |                |
  |                 [Step 5: update cache]          |                     |                  |                |
  |                       |-- HSET task:{id} --------------------------------------------->|                |
  |                       |   status=COMPLETED     |                     |                  |                |
  |                       |                        |                     |                  |                |
  |<-- {COMPLETED} -------|                        |                     |                  |                |
```

**DB calls: 2 PG** (mark running + store result) + **1 TB** (capture) + **1 Redis** (cache update) + **1 compute service call**.

Crash recovery: if the process crashes between step 3 (capture) and step 4 (store result), Restate replays the handler. Steps 1-3 are skipped (journaled results returned). Step 4 re-executes (idempotent UPDATE). No outbox. No compensation. No data loss.

---

## 4. Poll (`GET /v1/poll`)

```text
Client              API                     Redis                PG (tasks)
  |                   |                       |                      |
  |-- GET /v1/poll -->|                       |                      |
  |   ?task_id=xxx    |                       |                      |
  |                   |                       |                      |
  |                   |-- HGETALL task:{id} ->|                      |
  |                   |<-- {status, result} --|  (cache hit)         |
  |                   |                       |                      |
  |<-- 200 {status} --|                       |                      |
  |                   |                       |                      |
  |  OR (cache miss): |                       |                      |
  |                   |-- SELECT * FROM tasks ------------------>|
  |                   |<-- row ----------------------------------|
  |                   |-- HSET task:{id} ---->|  (backfill cache)    |
  |                   |                       |                      |
  |<-- 200 {status} --|                       |                      |
```

**DB calls: 0** (cache hit) or **1 PG** (cache miss, backfilled). This is the same zero-PG pattern as Sol 0/1 for the happy path (5 polls per task, first may miss, rest hit cache).

---

## 5. Cancel (`POST /v1/task/{id}/cancel`)

```text
Client          API              PG (tasks)          TigerBeetle         Redis
  |               |                   |                    |                  |
  |-- POST ------>|                   |                    |                  |
  | /cancel       |                   |                    |                  |
  |               |                   |                    |                  |
  |         [auth: 0-1 DB calls]      |                    |                  |
  |               |                   |                    |                  |
  |               |-- SELECT task --->|                    |                  |
  |               |<-- row -----------|                    |                  |
  |               |                   |                    |                  |
  |               |  [verify ownership + status=PENDING]   |                  |
  |               |  [403 if not owner, 409 if not PENDING]|                  |
  |               |                   |                    |                  |
  |               |-- void_pending_transfer ----------->|                  |
  |               |   (credits return to user)          |                  |
  |               |<-- ok ------------------------------|                  |
  |               |                   |                    |                  |
  |               |-- UPDATE status ->|                    |                  |
  |               |   = 'CANCELLED'   |                    |                  |
  |               |                   |                    |                  |
  |               |-- DEL task:{id} ----------------------------->|
  |               |                   |                    |                  |
  |<-- 200 -------|                   |                    |                  |
  | {task_id,     |                   |                    |                  |
  |  CANCELLED,   |                   |                    |                  |
  |  credits_     |                   |                    |                  |
  |  refunded}    |                   |                    |                  |
```

**DB calls: 2 PG** (SELECT + UPDATE) + **1 TB** (void) + **1 Redis** (invalidate).

No outbox needed. TigerBeetle void is atomic (credits return to user immediately). PG UPDATE is idempotent. Redis invalidation ensures next poll reads fresh state.

---

## 6. Admin credits (`POST /v1/admin/credits`)

```text
Client          API              TigerBeetle         PG (users)          Redis
  |               |                    |                   |                  |
  |-- POST ------>|                    |                   |                  |
  | {user_id,     |                    |                   |                  |
  |  amount}      |                    |                   |                  |
  |               |                    |                   |                  |
  |         [auth: 0-1 DB calls]       |                   |                  |
  |               |                    |                   |                  |
  |               |-- ensure_user_account                  |                  |
  |               |   (idempotent) --->|                   |                  |
  |               |                    |                   |                  |
  |               |-- direct_transfer  |                   |                  |
  |               |   (revenue → user) |                   |                  |
  |               |   amount=N ------->|                   |                  |
  |               |<-- ok -------------|                   |                  |
  |               |                    |                   |                  |
  |               |-- lookup_accounts  |                   |                  |
  |               |   → new_balance -->|                   |                  |
  |               |<-- balance --------|                   |                  |
  |               |                    |                   |                  |
  |               |-- UPDATE credits ------------------->|                  |
  |               |   = new_balance                       |                  |
  |               |                    |                   |                  |
  |<-- 200 -------|                    |                   |                  |
  | {user_id,     |                    |                   |                  |
  |  new_balance} |                    |                   |                  |
```

**DB calls: 1 PG** (mirror balance) + **2 TB** (ensure account + transfer + lookup) + **0 Redis**.

TigerBeetle is the source of truth for the balance. The PG `users.credits` column is a read-only mirror for convenience queries.

---

## DB call summary

| Endpoint                | PG calls | TB calls | Redis calls | Restate calls | Total |
| ----------------------- | -------- | -------- | ----------- | ------------- | ----- |
| Auth (cache hit)        | 0        | 0        | 1           | 0             | 1     |
| Auth (cache miss)       | 1        | 0        | 2           | 0             | 3     |
| Submit                  | 1        | 1        | 1           | 1             | 4     |
| Workflow (Restate)      | 2        | 1        | 1           | 0             | 4     |
| Poll (cache hit)        | 0        | 0        | 1           | 0             | 1     |
| Poll (cache miss)       | 1        | 0        | 1           | 0             | 2     |
| Cancel                  | 2        | 1        | 1           | 0             | 4     |
| Admin credits           | 1        | 2        | 0           | 0             | 3     |

**Typical task lifecycle** (submit + 5 polls + workflow complete): ~4 PG, ~2 TB, ~7 Redis, ~1 Restate = 14 total store calls. Of the 5 polls, typically 1 cache miss + 4 cache hits.

Compare: Sol 0 naive (PG-only) would be ~12 PG calls for the same lifecycle. Sol 4 replaces most PG calls with TB (billing) and Redis (cache), keeping PG for metadata only.
