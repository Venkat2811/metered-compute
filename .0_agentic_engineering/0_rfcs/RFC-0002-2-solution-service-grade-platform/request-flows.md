# RFC-0002: Request Flow Diagrams

Parent: [RFC-0002 README](./README.md)

Every diagram below shows the exact sequence of store calls for one API request. The "DB calls on happy path" count directly answers the core design question: _"how can we reduce the number of calls?"_

Column convention: Client, API, PG (cmd+query schemas), RabbitMQ, Redis. Arrows terminate at the column that owns the operation.

---

## 1. OAuth token acquisition (`POST /v1/oauth/token`)

```text
Client                 API                    Hydra                  PG (api_keys)
  |                      |                      |                      |
  |-- POST token ------->|                      |                      |
  |  {api_key:"..."}     |                      |                      |
  |                      |                      |                      |
  |                      |-- SELECT EXISTS ----->|----- hash+query --->|
  |                      |   api_keys.key_hash  |   =sha256()         |
  |                      |<--- true ------------|<--- exists ---------|
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
  |                    decode + verify RS256            |
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

**DB calls: 0** (when Redis is up). Auth is local JWT crypto + 1 Redis RTT (pipelined) for day-sharded revocation check. If Redis is unavailable, revocation falls back to Postgres `token_revocations` table (1 DB call).

---

## 3. Submit path (`POST /v1/task`) -- transactional outbox

```text
Client              API                    PG (cmd schema)        RabbitMQ            Redis
  |                   |                      |                      |                   |
  |-- POST /v1/task ->|                      |                      |                   |
  |  {x:5, y:3,       |                      |                      |                   |
  |   mode:"async"}   |                      |                      |                   |
  |                   |                      |                      |                   |
  |           [JWT auth: 0 DB calls, 1 Redis RTT for revocation]    |                   |
  |                   |                      |                      |                   |
  |                   |== BEGIN txn ========>|                      |                   |
  |                   |                      |                      |                   |
  |                   |  1. Idempotency      |                      |                   |
  |                   |-- SELECT task_id --->|                      |                   |
  |                   |   WHERE user_id=$1   |                      |                   |
  |                   |   AND idem_key=$2    |                      |                   |
  |                   |<-- NULL (no dup) ----|                      |                   |
  |                   |                      |                      |                   |
  |                   |  2. Concurrency      |                      |                   |
  |                   |-- SELECT COUNT(*) -->|                      |                   |
  |                   |   credit_reservations|                      |                   |
  |                   |   WHERE state=       |                      |                   |
  |                   |   'RESERVED'         |                      |                   |
  |                   |<-- count < max ------|                      |                   |
  |                   |                      |                      |                   |
  |                   |  3. Reserve credits  |                      |                   |
  |                   |-- UPDATE users ----->|                      |                   |
  |                   |   credits=credits-$1 |                      |                   |
  |                   |   WHERE credits>=$1  |                      |                   |
  |                   |<-- UPDATE 1 ---------|                      |                   |
  |                   |                      |                      |                   |
  |                   |-- INSERT             |                      |                   |
  |                   |   credit_reservations|                      |                   |
  |                   |   (RESERVED,         |                      |                   |
  |                   |    expires +10min)-->|                      |                   |
  |                   |                      |                      |                   |
  |                   |  4. Command row      |                      |                   |
  |                   |-- INSERT             |                      |                   |
  |                   |   task_commands ---->|                      |                   |
  |                   |   (task_id, user_id, |                      |                   |
  |                   |    tier, mode, x, y, |                      |                   |
  |                   |    cost, idem_key)   |                      |                   |
  |                   |                      |                      |                   |
  |                   |  5. Outbox event     |                      |                   |
  |                   |-- INSERT             |                      |                   |
  |                   |   outbox_events ---->|                      |                   |
  |                   |   (task.requested,   |                      |                   |
  |                   |    routing_key,      |                      |                   |
  |                   |    payload)          |                      |                   |
  |                   |                      |                      |                   |
  |                   |== COMMIT ===========>|                      |                   |
  |                   |                      |                      |                   |
  |                   |  6. Write-through cache                     |                   |
  |                   |-- HSET task:{tid} --|--------------------->|--> PENDING ------->|
  |                   |-- EXPIRE 86400 -----|--------------------->|------------------>|
  |                   |                      |                      |                   |
  |<-- 201 {task_id,  |                      |                      |                   |
  |     queue} --------|                      |                      |                   |
  |                   |                      |                      |                   |
  |                   |                      |                      |                   |
  |           [LATER: outbox relay picks up the event]              |                   |
  |                   |                      |                      |                   |
  |              Relay |-- SELECT unpublished>|                      |                   |
  |                   |<-- outbox rows ------|                      |                   |
  |                   |-- basic_publish -----|------- msg --------->|                   |
  |                   |   exchange="tasks"   |  routing_key=        |                   |
  |                   |                      |  tasks.async.pro.med |                   |
  |                   |-- UPDATE published_at>|                      |                   |
```

**DB calls: 1** (single PG transaction: idempotency check + concurrency check + credit reserve + INSERT task_commands + INSERT credit_reservations + INSERT outbox_events -- all in one COMMIT). Redis write-through is a non-blocking follow-up. The outbox relay publishes to RabbitMQ asynchronously.

Key difference vs solution 1: the entire admission gate (idempotency, concurrency, credits) runs inside the PG transaction instead of a Redis Lua script. No dual-write risk -- the outbox event is in the same transaction as the command state.

---

## 4. Poll path (`GET /v1/poll`) -- query side

```text
Client              API                    PG (query schema)      RabbitMQ            Redis
  |                   |                      |                      |                   |
  |-- GET /v1/poll -->|                      |                      |                   |
  |  ?task_id=X       |                      |                      |                   |
  |                   |                      |                      |                   |
  |           [JWT auth: 0 DB calls, 1 Redis RTT for revocation]    |                   |
  |                   |                      |                      |                   |
  |           TIER 1: Redis query cache      |                      |                   |
  |                   |-- HGETALL task:{tid}>|--------------------->|----------------->|
  |                   |                      |                      |                   |
  |           [hit?] YES: check user_id match|                      |                   |
  |                   |<-- {status, user_id, |--------------------->|<-- hash data ----|
  |                   |     result} ---------|                      |                   |
  |<-- 200 response --|                      |                      |                   |
  |                   |                      |                      |                   |
  |           [miss] TIER 2: PG query view   |                      |                   |
  |                   |-- SELECT * FROM ---->|                      |                   |
  |                   |   query.             |                      |                   |
  |                   |   task_query_view    |                      |                   |
  |                   |   WHERE task_id=$1   |                      |                   |
  |                   |<-- row --------------|                      |                   |
  |<-- 200 response --|                      |                      |                   |
```

**DB calls: 0** on Redis cache hit (typical -- submit path writes `task:{id}` immediately, worker updates it on completion). **DB calls: 1** on cache miss (query view fallback -- task expired from Redis or Redis restarted).

vs solution 1: same 0 DB calls on happy path. The difference is the cache miss fallback reads from `query.task_query_view` (a projected read model) instead of the command table directly.

---

## 5. Cancel path (`POST /v1/task/{id}/cancel`) -- release reservation

```text
Client              API                    PG (cmd schema)        RabbitMQ            Redis
  |                   |                      |                      |                   |
  |-- POST cancel --->|                      |                      |                   |
  |                   |                      |                      |                   |
  |           [JWT auth: 0 DB calls, 1 Redis RTT for revocation]    |                   |
  |                   |                      |                      |                   |
  |                   |== BEGIN txn ========>|                      |                   |
  |                   |                      |                      |                   |
  |                   |  1. Lock reservation |                      |                   |
  |                   |-- SELECT amount ---->|                      |                   |
  |                   |   credit_reservations|                      |                   |
  |                   |   WHERE task_id=$1   |                      |                   |
  |                   |   AND user_id=$2     |                      |                   |
  |                   |   AND state=RESERVED |                      |                   |
  |                   |   FOR UPDATE         |                      |                   |
  |                   |<-- {res_id, amount} -|                      |                   |
  |                   |                      |                      |                   |
  |                   |  [not found? -> 409 Not cancellable]        |                   |
  |                   |                      |                      |                   |
  |                   |  2. Release credits  |                      |                   |
  |                   |-- UPDATE             |                      |                   |
  |                   |   credit_reservations|                      |                   |
  |                   |   SET state=RELEASED |                      |                   |
  |                   |   WHERE res_id=$1 -->|                      |                   |
  |                   |                      |                      |                   |
  |                   |  3. Refund user      |                      |                   |
  |                   |-- UPDATE users ----->|                      |                   |
  |                   |   credits=credits+$1 |                      |                   |
  |                   |                      |                      |                   |
  |                   |  4. Update command   |                      |                   |
  |                   |-- UPDATE             |                      |                   |
  |                   |   task_commands ----->|                      |                   |
  |                   |   SET status=         |                      |                   |
  |                   |   CANCELLED          |                      |                   |
  |                   |                      |                      |                   |
  |                   |  5. Audit trail      |                      |                   |
  |                   |-- INSERT             |                      |                   |
  |                   |   credit_transactions|                      |                   |
  |                   |   (delta=+amount,    |                      |                   |
  |                   |    reason=           |                      |                   |
  |                   |    cancel_release)-->|                      |                   |
  |                   |                      |                      |                   |
  |                   |== COMMIT ===========>|                      |                   |
  |                   |                      |                      |                   |
  |                   |  6. Update Redis     |                      |                   |
  |                   |-- HSET task:{tid} --|--------------------->|--> CANCELLED ----->|
  |                   |   status=CANCELLED   |                      |                   |
  |                   |                      |                      |                   |
  |<-- 200            |                      |                      |                   |
  |  {credits_refunded|                      |                      |                   |
  |   : amount}       |                      |                      |                   |
```

**DB calls: 1** (single PG transaction: SELECT reservation FOR UPDATE + UPDATE reservation RELEASED + UPDATE users credits + UPDATE task_commands CANCELLED + INSERT credit_transactions). Redis status update is a non-blocking follow-up.

vs solution 1: solution 1 has 2 DB calls (SELECT task for ownership + transaction). Here the ownership check is embedded in the reservation SELECT (WHERE user_id=$2), so it is 1 transaction total.

---

## 6. Worker execution -- consume from RabbitMQ

### Happy path (success)

```text
RabbitMQ            Worker                 PG (cmd schema)        Redis
  |                   |                      |                      |
  |-- deliver msg --->|                      |                      |
  |   (task.requested)|                      |                      |
  |                   |                      |                      |
  |                   |  [inbox dedup check] |                      |
  |                   |-- SELECT event_id -->|                      |
  |                   |   FROM inbox_events  |                      |
  |                   |<-- NULL (new) -------|                      |
  |                   |                      |                      |
  |                   |  [compute]           |                      |
  |                   |  model(x,y) -> result|                      |
  |                   |  (~2-10s)            |                      |
  |                   |                      |                      |
  |                   |== BEGIN txn ========>|                      |
  |                   |                      |                      |
  |                   |  1. Lock reservation |                      |
  |                   |-- SELECT res ------->|                      |
  |                   |   credit_reservations|                      |
  |                   |   WHERE task_id=$1   |                      |
  |                   |   AND state=RESERVED |                      |
  |                   |   FOR UPDATE         |                      |
  |                   |<-- {res_id, user_id, |                      |
  |                   |     amount} ---------|                      |
  |                   |                      |                      |
  |                   |  2. Capture credits  |                      |
  |                   |-- UPDATE             |                      |
  |                   |   credit_reservations|                      |
  |                   |   SET state=CAPTURED |                      |
  |                   |   WHERE task_id=$1-->|                      |
  |                   |                      |                      |
  |                   |  3. Audit trail      |                      |
  |                   |-- INSERT             |                      |
  |                   |   credit_transactions|                      |
  |                   |   (delta=-amount,    |                      |
  |                   |    reason=capture)-->|                      |
  |                   |                      |                      |
  |                   |  4. Update command   |                      |
  |                   |-- UPDATE             |                      |
  |                   |   task_commands ----->|                      |
  |                   |   SET status=         |                      |
  |                   |   COMPLETED          |                      |
  |                   |                      |                      |
  |                   |  5. Inbox record     |                      |
  |                   |-- INSERT             |                      |
  |                   |   inbox_events ----->|                      |
  |                   |   (event_id,         |                      |
  |                   |    consumer_name)    |                      |
  |                   |                      |                      |
  |                   |== COMMIT ===========>|                      |
  |                   |                      |                      |
  |                   |  6. Update Redis     |                      |
  |                   |-- HSET task:{tid} --|--------------------->|
  |                   |   status=COMPLETED,  |                      |
  |                   |   result=...         |                      |
  |                   |                      |                      |
  |                   |  7. Webhook?         |                      |
  |                   |-- SELECT callback -->|                      |
  |                   |   FROM task_commands |                      |
  |                   |<-- callback_url -----|                      |
  |                   |                      |                      |
  |                   |  [callback_url exists]                      |
  |<-- basic_publish--|                      |                      |
  |   exchange=       |                      |                      |
  |   "webhooks"      |                      |                      |
  |   routing_key=    |                      |                      |
  |   "deliver"       |                      |                      |
  |                   |                      |                      |
  |<-- basic_ack -----|                      |                      |
```

**DB calls: 1** (single PG transaction: inbox dedup + SELECT reservation FOR UPDATE + UPDATE reservation CAPTURED + INSERT credit_transactions + UPDATE task_commands COMPLETED + INSERT inbox_events). The callback_url SELECT is a read outside the transaction but could be folded in. Redis update is a non-blocking follow-up.

### Failure path (release reservation)

```text
RabbitMQ            Worker                 PG (cmd schema)        Redis
  |                   |                      |                      |
  |-- deliver msg --->|                      |                      |
  |                   |                      |                      |
  |                   |  [exception during   |                      |
  |                   |   model() execution] |                      |
  |                   |                      |                      |
  |                   |== BEGIN txn ========>|                      |
  |                   |                      |                      |
  |                   |  1. Lock reservation |                      |
  |                   |-- SELECT res ------->|                      |
  |                   |   FOR UPDATE         |                      |
  |                   |<-- {res_id, user_id, |                      |
  |                   |     amount} ---------|                      |
  |                   |                      |                      |
  |                   |  2. Release credits  |                      |
  |                   |-- UPDATE             |                      |
  |                   |   credit_reservations|                      |
  |                   |   SET state=RELEASED |                      |
  |                   |   WHERE task_id=$1-->|                      |
  |                   |                      |                      |
  |                   |  3. Refund user      |                      |
  |                   |-- UPDATE users ----->|                      |
  |                   |   credits=credits+$1 |                      |
  |                   |                      |                      |
  |                   |  4. Audit trail      |                      |
  |                   |-- INSERT             |                      |
  |                   |   credit_transactions|                      |
  |                   |   (delta=+amount,    |                      |
  |                   |    reason=release)-->|                      |
  |                   |                      |                      |
  |                   |  5. Update command   |                      |
  |                   |-- UPDATE             |                      |
  |                   |   task_commands ----->|                      |
  |                   |   SET status=FAILED  |                      |
  |                   |                      |                      |
  |                   |== COMMIT ===========>|                      |
  |                   |                      |                      |
  |                   |  6. Update Redis     |                      |
  |                   |-- HSET task:{tid} --|--------------------->|
  |                   |   status=FAILED      |                      |
  |                   |                      |                      |
  |<-- basic_ack -----|                      |                      |
```

**DB calls: 1** (single PG transaction: release reservation + refund credits + audit + mark FAILED). Credits are returned to the user atomically. No refund job needed -- the reservation model handles it.

---

## 7. Outbox relay loop (polls PG, publishes to RabbitMQ)

```text
PG (cmd.outbox_events)     Relay                  RabbitMQ
  |                          |                      |
  |  [tick every ~1s]        |                      |
  |                          |                      |
  |<-- SELECT event_id, ----|                      |
  |    routing_key, payload  |                      |
  |    FROM outbox_events    |                      |
  |    WHERE published_at    |                      |
  |    IS NULL               |                      |
  |    ORDER BY created_at   |                      |
  |    LIMIT 100             |                      |
  |--- rows[] ------------->|                      |
  |                          |                      |
  |  [for each row:]        |                      |
  |                          |                      |
  |                          |-- basic_publish ---->|
  |                          |   exchange="tasks"   |
  |                          |   routing_key=       |
  |                          |   row.routing_key    |
  |                          |   delivery_mode=2    |
  |                          |   (persistent)       |
  |                          |<-- publisher confirm-|
  |                          |                      |
  |<-- UPDATE outbox_events -|                      |
  |    SET published_at=now()|                      |
  |    WHERE event_id=$1     |                      |
  |--- OK ----------------->|                      |
  |                          |                      |
  |  [next row...]          |                      |
  |                          |                      |
  |  [all rows published]   |                      |
  |                          |                      |
  |  [sleep 1s, repeat]     |                      |
```

The relay marks each event published only AFTER receiving the RabbitMQ publisher confirm. If the relay crashes mid-batch, unpublished rows remain with `published_at IS NULL` and are retried on restart. Consumers deduplicate via inbox_events table, so re-publishing the same event is safe.

Index `idx_outbox_unpublished` on `(published_at) WHERE published_at IS NULL` keeps the SELECT fast as the table grows.

---

## 8. Watchdog cycle -- expired reservation release + result expiry

```text
PG (cmd schema)            Watchdog               Redis
  |                          |                      |
  |  [tick every ~30s]       |                      |
  |                          |                      |
  |== PHASE 1: Expired reservation release =========|
  |                          |                      |
  |<-- SELECT reservation_id,|                      |
  |    task_id, user_id,     |                      |
  |    amount                |                      |
  |    FROM credit_reservations                     |
  |    WHERE state='RESERVED'|                      |
  |    AND expires_at < now()|                      |
  |    FOR UPDATE SKIP LOCKED|                      |
  |--- expired rows[] ----->|                      |
  |                          |                      |
  |  [for each expired row:] |                      |
  |                          |                      |
  |<== BEGIN txn ============|                      |
  |                          |                      |
  |<-- UPDATE                |                      |
  |    credit_reservations   |                      |
  |    SET state=RELEASED    |                      |
  |    WHERE res_id=$1 -----|                      |
  |                          |                      |
  |<-- UPDATE users          |                      |
  |    credits=credits+$1 --|                      |
  |                          |                      |
  |<-- UPDATE task_commands  |                      |
  |    SET status=TIMEOUT ---|                      |
  |                          |                      |
  |<-- INSERT                |                      |
  |    credit_transactions   |                      |
  |    (delta=+amount,       |                      |
  |     reason=              |                      |
  |     timeout_release) ----|                      |
  |                          |                      |
  |=== COMMIT ===============|                      |
  |                          |                      |
  |                          |-- HSET task:{tid} -->|
  |                          |   status=TIMEOUT     |
  |                          |                      |
  |  [next expired row...]   |                      |
  |                          |                      |
  |== PHASE 2: Result expiry =======================|
  |                          |                      |
  |<-- UPDATE task_commands  |                      |
  |    SET status='EXPIRED'  |                      |
  |    WHERE status IN       |                      |
  |    ('COMPLETED','FAILED')|                      |
  |    AND updated_at <      |                      |
  |    now() - 24h ---------|                      |
  |                          |                      |
  |<-- UPDATE                |                      |
  |    task_query_view       |                      |
  |    SET status='EXPIRED'  |                      |
  |    WHERE status IN       |                      |
  |    ('COMPLETED','FAILED')|                      |
  |    AND updated_at <      |                      |
  |    now() - 24h ---------|                      |
```

Phase 1 runs per-row transactions with `FOR UPDATE SKIP LOCKED` so multiple watchdog instances do not contend. Phase 2 is a bulk UPDATE across both command and query tables.

vs solution 1 reaper: no orphan marker recovery (no pending markers in Redis), no credit snapshot flush (PG is the billing authority), no credit drift audit (no Redis-PG divergence risk).

---

## 9. Admin credits (`POST /v1/admin/credits`)

```text
Client              API                    PG (cmd schema)        RabbitMQ            Redis
  |                   |                      |                      |                   |
  |-- POST credits -->|                      |                      |                   |
  |  {user_id, delta, |                      |                      |                   |
  |   reason}         |                      |                      |                   |
  |                   |                      |                      |                   |
  |           [JWT auth: verify admin role + admin_credits scope]    |                   |
  |                   |                      |                      |                   |
  |                   |== BEGIN txn ========>|                      |                   |
  |                   |                      |                      |                   |
  |                   |  1. Update balance   |                      |                   |
  |                   |-- UPDATE users ----->|                      |                   |
  |                   |   credits=credits+$1 |                      |                   |
  |                   |   WHERE user_id=$2   |                      |                   |
  |                   |   RETURNING credits  |                      |                   |
  |                   |<-- new_balance ------|                      |                   |
  |                   |                      |                      |                   |
  |                   |  2. Audit trail      |                      |                   |
  |                   |-- INSERT             |                      |                   |
  |                   |   credit_transactions|                      |                   |
  |                   |   (delta, reason) -->|                      |                   |
  |                   |                      |                      |                   |
  |                   |  3. Outbox event     |                      |                   |
  |                   |-- INSERT             |                      |                   |
  |                   |   outbox_events ---->|                      |                   |
  |                   |   (credits.adjusted, |                      |                   |
  |                   |    payload)          |                      |                   |
  |                   |                      |                      |                   |
  |                   |== COMMIT ===========>|                      |                   |
  |                   |                      |                      |                   |
  |                   |  4. Sync Redis cache |                      |                   |
  |                   |-- SET credits:{uid}--|--------------------->|--> new_balance --->|
  |                   |                      |                      |                   |
  |<-- 200            |                      |                      |                   |
  |  {new_balance}    |                      |                      |                   |
```

**DB calls: 1** (single PG transaction: UPDATE users + INSERT credit_transactions + INSERT outbox_events). Redis sync is a non-blocking follow-up. The outbox event allows downstream projectors to react to credit changes if needed.

---

## 10. Webhook delivery -- via RabbitMQ

```text
Worker              RabbitMQ               Webhook Worker         External Service
  |                   |                      |                      |
  |-- basic_publish ->|                      |                      |
  |   exchange=       |                      |                      |
  |   "webhooks"      |                      |                      |
  |   routing_key=    |                      |                      |
  |   "deliver"       |                      |                      |
  |   headers:        |                      |                      |
  |     target_url=   |                      |                      |
  |     "https://..." |                      |                      |
  |   body={task_id,  |                      |                      |
  |     status,result}|                      |                      |
  |                   |                      |                      |
  |                   |-- deliver msg ------>|                      |
  |                   |                      |                      |
  |                   |                      |-- POST callback_url->|
  |                   |                      |   {task_id, status,  |
  |                   |                      |    result}           |
  |                   |                      |                      |
  |                   |                      |  [2xx response?]     |
  |                   |                      |<-- 200 OK -----------|
  |                   |<-- basic_ack --------|                      |
  |                   |                      |                      |
  |                   |  [non-2xx / timeout?]|                      |
  |                   |                      |<-- 5xx / timeout ----|
  |                   |<-- basic_nack -------|                      |
  |                   |   (requeue=false)    |                      |
  |                   |                      |                      |
  |                   |-- route to DLQ ----->|                      |
  |                   |   webhooks.dlq       |                      |
  |                   |   (x-message-ttl     |                      |
  |                   |    for retry backoff)|                      |
```

Webhook delivery is fully decoupled from the worker execution path. The worker publishes a message to the `webhooks` exchange after completing a task. The webhook worker consumes and delivers. On failure, the message is routed to a DLQ with TTL-based retry backoff. DLQ messages expire after 7 days.

---

## 11. Projection path -- via RabbitMQ

```text
RabbitMQ            Projector              PG (query schema)      Redis
  |                   |                      |                      |
  |-- deliver msg --->|                      |                      |
  |   (task.requested |                      |                      |
  |    or task.       |                      |                      |
  |    completed etc.)|                      |                      |
  |                   |                      |                      |
  |                   |  [inbox dedup check] |                      |
  |                   |-- SELECT event_id -->|                      |
  |                   |   FROM inbox_events  |                      |
  |                   |<-- NULL (new) -------|                      |
  |                   |                      |                      |
  |                   |== BEGIN txn ========>|                      |
  |                   |                      |                      |
  |                   |-- UPSERT             |                      |
  |                   |   query.             |                      |
  |                   |   task_query_view    |                      |
  |                   |   ON CONFLICT        |                      |
  |                   |   (task_id)          |                      |
  |                   |   DO UPDATE -------->|                      |
  |                   |   SET status=$2,     |                      |
  |                   |   result=$3,         |                      |
  |                   |   updated_at=now()   |                      |
  |                   |                      |                      |
  |                   |-- INSERT             |                      |
  |                   |   inbox_events ----->|                      |
  |                   |   (event_id,         |                      |
  |                   |    consumer=         |                      |
  |                   |    "projector")      |                      |
  |                   |                      |                      |
  |                   |== COMMIT ===========>|                      |
  |                   |                      |                      |
  |<-- basic_ack -----|                      |                      |
```

The projector maintains the query-side read model (`query.task_query_view`) from events published via the outbox relay. The inbox dedup prevents double-processing if the same event is delivered twice. The query view can always be rebuilt from the command table via SQL join -- no event log needed.

Note: the worker already writes to Redis directly (diagram 6), so clients see results immediately via poll. The projector updates the PG query view for cache-miss fallback and for any reporting queries that go to Postgres directly.

---

## 12. DB call summary table

| Path                          | PG calls (happy) | Redis calls | RabbitMQ calls | Notes                                                           |
| ----------------------------- | :--------------: | :---------: | :------------: | --------------------------------------------------------------- |
| OAuth token acquisition       |        1         |      0      |       0        | api_key hash validation only                                    |
| JWT auth middleware            |        0         |      1      |       0        | Local crypto + 1 pipelined Redis RTT for revocation             |
| Submit (`POST /v1/task`)      |        1         |      1      |       0        | Single PG txn (5 ops). Redis write-through. RabbitMQ via relay. |
| Poll (`GET /v1/poll`)         |      0 / 1       |      1      |       0        | 0 on Redis hit, 1 on miss (query view fallback)                 |
| Cancel (`POST /v1/task/{id}`) |        1         |      1      |       0        | Single PG txn (5 ops). Redis status update.                     |
| Worker (success)              |        1         |      1      |       1        | Single PG txn (capture + audit). Webhook publish if callback.   |
| Worker (failure)              |        1         |      1      |       0        | Single PG txn (release + refund + audit).                       |
| Outbox relay (per batch)      |        2         |      0      |       N        | SELECT unpublished + UPDATE published_at per event.             |
| Watchdog (per expired)        |        1         |      1      |       0        | Per-reservation PG txn + Redis update. Bulk result expiry.      |
| Admin credits                 |        1         |      1      |       0        | Single PG txn (balance + audit + outbox). Redis sync.           |
| Webhook delivery              |        0         |      0      |       1        | Consume from RabbitMQ, POST to external. DLQ on failure.        |
| Projection                    |        1         |      0      |       1        | UPSERT query view + inbox dedup. Consume from RabbitMQ.         |

### Comparison vs solution 1

| Path   | Sol 0 (Celery) | Sol 1 (Redis-native) | Sol 2 (CQRS+outbox) |
| ------ | :------------: | :------------------: | :-----------------: |
| Submit |       1        |          1           |          1          |
| Poll   |     0 / 1      |        0 / 1         |        0 / 1        |
| Cancel |       1        |          2           |          1          |
| Worker |       2        |          3           |          1          |

Solution 2 trades the Redis Lua admission gate for a PG transaction, gaining transactional guarantees (no dual-write, no compensation logic, no refund jobs) at the same DB call count. The worker path improves from 3 DB calls (Sol 1: PENDING->RUNNING + RUNNING->COMPLETED + checkpoint) to 1 DB call (single capture/release transaction) because the reservation model eliminates the two-phase status transition.
