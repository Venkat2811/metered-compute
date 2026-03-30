# RFC-0004: Data Ownership and Consistency

Parent: [RFC-0004 README](./README.md)

## Data ownership model

Solution 4 uses four stores with clear ownership boundaries. TigerBeetle is the source of truth for billing. Postgres is the source of truth for metadata. Redis is a cache. Restate is a workflow journal.

| Data                    | TigerBeetle              | Postgres                      | Redis                       | Restate              | Source of truth | On component restart                         |
| ----------------------- | ------------------------ | ----------------------------- | --------------------------- | -------------------- | --------------- | -------------------------------------------- |
| **User identity**       | -                        | `users`, `api_keys`           | -                           | -                    | Postgres        | N/A; always in PG                            |
| **Credit balance**      | User account (u128 ID)   | `users.credits` (mirror only) | -                           | -                    | TigerBeetle     | TB survives restart; PG mirror re-synced     |
| **Pending reservation** | Pending transfer         | -                             | -                           | -                    | TigerBeetle     | Pending transfers survive; auto-void on timeout |
| **Credit audit trail**  | Transfer log (built-in)  | -                             | -                           | -                    | TigerBeetle     | TB transfer log is immutable                 |
| **Task metadata**       | -                        | `tasks` (status, result, x, y)| `task:{id}` hash (cache)    | -                    | Postgres        | Cache miss falls through to PG               |
| **Task lifecycle**      | -                        | -                             | -                           | Invocation journal   | Restate         | Replay from last journaled step              |
| **Compute result**      | -                        | Stored after completion       | Cached after completion     | Not a store          | Compute worker → PG | Recomputed if workflow step is replayed   |
| **Auth cache**          | -                        | `api_keys` + `users` (source) | `auth:{hash}` (TTL 300s)   | -                    | Postgres        | Cache expires; next request re-populates     |
| **Idempotency**         | -                        | Unique index `(user_id, idempotency_key)` | -           | -                    | Postgres        | Constraint survives restart                  |
| **Platform accounts**   | Revenue + Escrow accounts| -                             | -                           | -                    | TigerBeetle     | Created idempotently on startup              |

Key difference from Sol 0/1/2: **TigerBeetle is the billing authority, not Postgres.** The PG `users.credits` column is a read-only mirror updated after TB operations. Redis is never the billing authority. Restate is never a data store — it only journals workflow execution state.

## Consistency boundaries

### Atomic boundary 1: TigerBeetle (billing)

All credit operations are atomic within TigerBeetle:

```text
pending_transfer(user → escrow)     -- atomic debit + credit
post_pending_transfer               -- atomic capture
void_pending_transfer               -- atomic reversal
direct_transfer(revenue → user)     -- atomic topup
```

TigerBeetle enforces `debits_must_not_exceed_credits` at the storage engine level. No application code can cause an overdraft. No `SELECT ... FOR UPDATE` needed. No transaction isolation level tuning.

### Atomic boundary 2: Postgres (metadata)

Task creation is a single INSERT. Status updates are single UPDATEs. No multi-table transactions needed because billing is not in Postgres.

Compare to Sol 2 where a submit requires a 6-statement PG transaction (idempotency check + concurrency check + credit deduction + reservation insert + task insert + outbox insert).

### Cross-store boundary: API → TB → PG (submit path)

The submit path writes to TB first, then PG:

```text
1. TigerBeetle: create_pending_transfer (user → escrow)
   ├── Success → continue to step 2
   └── EXCEEDS_CREDITS → return 402 (no PG write)

2. Postgres: INSERT tasks (PENDING, tb_transfer_id)
   ├── Success → continue to step 3
   └── Failure → void TB transfer (compensate), return 500

3. Redis: HSET task:{id} (cache)
4. Restate: invoke workflow (async)
```

This is the only cross-store write in the codebase. The compensation path (void TB transfer on PG failure) is 3 lines of code. Compare to Sol 2's outbox pattern which requires a relay service, inbox dedup table, and published_at tracking to achieve the same cross-store guarantee.

### Cross-store boundary: Restate → PG + TB + Redis (workflow)

The Restate workflow writes to PG, TB, and Redis across multiple steps. Restate journals each step:

```text
Step 1: PG UPDATE tasks status='RUNNING'        (idempotent)
Step 2: ctx.run("compute")                       (journaled result)
Step 3: ctx.run("capture_credits")               (journaled TB post)
Step 4: PG UPDATE tasks status='COMPLETED'       (idempotent)
Step 5: Redis HSET task:{id}                     (cache update)
```

If the process crashes between step 3 and step 4, Restate replays from step 4. Steps 1-3 return their journaled results. No outbox table. No relay service. No compensation code.

## Degradation matrix

| Component down | Submit                                        | Poll                    | Cancel                      | Admin credits          | Workflow (Restate)                |
| -------------- | --------------------------------------------- | ----------------------- | --------------------------- | ---------------------- | --------------------------------- |
| **Redis**      | Auth cache miss (PG fallback); task cache skip | PG fallback (slower)    | Works (no Redis dependency) | Works                  | Cache update fails (stale cache)  |
| **Postgres**   | 500 (can't INSERT task)                       | Cache hit works; miss 503| 500 (can't SELECT/UPDATE)  | 500 (can't mirror)    | PG updates fail; Restate retries  |
| **TigerBeetle**| 500 (can't reserve credits)                   | Works (no TB dependency)| 500 (can't void transfer)  | 500 (can't transfer)  | Capture fails; Restate retries    |
| **Restate**    | Task created (PENDING); workflow won't start  | Works                   | Works                       | Works                  | N/A                               |

Key properties:

- **Redis down**: degraded latency, not data loss. PG fallback for auth and poll.
- **PG down**: submit and cancel fail (metadata required). Poll works from Redis cache.
- **TB down**: all billing operations fail. Poll works. Auth works.
- **Restate down**: tasks stay PENDING. No workflow execution. Submit still works (task row created, pending transfer held). When Restate recovers, workflows resume.
- **TB pending transfer timeout (300s)**: if Restate never captures, TB auto-voids the transfer. Credits return to user. No watchdog needed.

## Data retention

| Data                 | Store       | Retention                         |
| -------------------- | ----------- | --------------------------------- |
| Users + API keys     | Postgres    | Indefinite                        |
| Tasks                | Postgres    | Indefinite (could prune completed after 90d) |
| Auth cache           | Redis       | TTL 300s                          |
| Task cache           | Redis       | TTL 3600s                         |
| Credit balances      | TigerBeetle | Indefinite (account state)        |
| Transfer log         | TigerBeetle | Indefinite (immutable audit trail)|
| Pending transfers    | TigerBeetle | Auto-void after 300s timeout      |
| Restate journal      | Restate     | Configurable; default indefinite  |
| Prometheus metrics   | Prometheus  | 15d (default retention)           |
