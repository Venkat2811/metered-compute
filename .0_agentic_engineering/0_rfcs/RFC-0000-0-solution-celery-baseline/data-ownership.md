# RFC-0000: Data Ownership and Consistency

Parent: [RFC-0000 README](./README.md)

## Data ownership model

Redis and Postgres share responsibility with a clear split: Redis owns the hot path, Postgres owns durability.

| Data                  | Redis (hot path)                  | Postgres (durable)          | Source of truth                                | On Redis restart                                                   |
| --------------------- | --------------------------------- | --------------------------- | ---------------------------------------------- | ------------------------------------------------------------------ |
| **User identity**     | `auth:{api_key}` hash (TTL 60s)   | `users` table               | Postgres                                       | Cache miss -> Postgres lookup -> repopulate                        |
| **Credit balance**    | `credits:{user_id}` integer       | `credit_snapshots` table    | **Redis during operation**, Postgres recovery  | Rehydrate via `CACHE_MISS`; up to one reaper interval can be lost  |
| **Credit audit**      | -                                 | `credit_transactions` table | Postgres (immutable log)                       | N/A; always in Postgres                                            |
| **Idempotency**       | `idem:{user_id}:{key}` (TTL 24h)  | -                           | Redis only                                     | Lost, but TTL-bounded and safe                                     |
| **Active task count** | `active:{user_id}` integer        | -                           | Redis only                                     | Resets to 0; may briefly allow over-limit until tasks settle       |
| **Task state**        | `result:{task_id}` hash (TTL 24h) | `tasks` table               | Postgres (guarded transitions)                 | Poll falls back to Postgres                                        |
| **Task queue**        | Celery broker (Redis lists)       | -                           | Redis only                                     | In-flight tasks may be lost; reaper recovers stuck `RUNNING` tasks |
| **Dirty markers**     | `credits:dirty` set               | -                           | Redis only                                     | Lost; next deduction re-adds keys to dirty set                     |

Key invariant: **credits are never over-charged.** Every failure mode results in under-charge (refund) or at-most-once deduction.

## Dual-write boundaries

Credit deduction (Redis Lua) and task publish (Celery) are NOT atomic. Three non-atomic boundaries exist:

```text
Lua deduction  -->  (gap 1)  -->  pending marker  -->  (gap 2)  -->  PG persist  -->  (gap 3)  -->  Celery publish
```

| Gap | What can go wrong                                   | Recovery                                                                                |
| --- | --------------------------------------------------- | --------------------------------------------------------------------------------------- |
| 1   | Crash after Lua deduction, before pending marker    | Idempotency prevents re-deduction; no marker means no reaper detection until retry/TTL  |
| 2   | Crash after pending marker, before Postgres persist | Reaper scans `pending:*`, finds orphan after 60s, refunds credits, and cleans marker    |
| 3   | Celery publish fails after Postgres persist         | API compensates immediately: `INCRBY` refund + `DECR` active + `DEL` idempotency key   |

Refund direction is always safe: under-charge, never over-charge.
This is a known limitation. Solution 2 solves it with the outbox pattern.

## Task state consistency

Postgres is the authority for task state. Redis caches are read-through optimizations:

```text
Submit   : Lua HSET task:{id}=PENDING  -> PG INSERT status=PENDING    (Redis leads by ~50ms)
Worker   : PG UPDATE status=RUNNING    -> Redis HSET result:{id}      (PG leads by ~1ms)
Complete : PG UPDATE status=COMPLETED  -> Redis HSET result:{id}      (PG leads by ~1ms)
```

The poll path checks Redis first, falls back to PG. If Redis has a stale status (e.g. still PENDING while PG says RUNNING), the client sees the stale status until the next poll. This is acceptable: the status only moves forward, and the next poll will see the updated state from Redis or PG.

## Auth cache consistency

Auth cache is eventually consistent with a 60s TTL. Admin credit updates explicitly invalidate the cache (`DEL auth:{api_key}`). Edge cases:

- User deleted from PG: cached auth works for up to 60s. Acceptable for this scope.
- Admin changes role: cache is invalidated on credit update, but not on role-only changes. Acceptable because role changes are rare and TTL-bounded.
