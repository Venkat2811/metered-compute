# P0-005 Solution 3 - Projections, Reconciler, and Webhook Worker

Objective:

Add query-side materialization and recovery mechanisms so Sol 3 is operational under stale states and infra churn.

Status: complete as of 2026-03-27. Redpanda task events project into `query.task_query_view`, inbox dedup is in place, projection checkpoints advance, live poll fallback works after deleting the Redis task key, the projection can be rebuilt either from SQL or by replaying Redpanda from offset `0`, stale `RESERVED` tasks reconcile either to `EXPIRED` or back to the correct TigerBeetle-backed terminal state, and terminal webhook callbacks deliver with bounded retries plus durable dead-letter capture.

Acceptance criteria:

- [x] Projector consumes command events into query view and checkpoints offsets.
- [x] Rebuilder mode can replay from topic start and restore query view.
- [x] Reconciler resolves stale reserved states and pending terminal drifts.
- [x] Webhook worker dispatches callbacks with retry/dead-letter policy.

TDD order:

1. Add projector unit tests around idempotent consumption and checkpoint progression.
2. Add reconciler tests with simulated stale transfer states.
3. Add webhook dispatch tests for retry and success path.
4. Implement services incrementally from projector upward.

Checklist:

- [x] Add `src/solution3/db/repository.py` query-side methods:
  - upsert into `query.task_query_view`
  - checkpoint reads/writes
  - projection audit helpers
- [x] Add `src/solution3/workers/projector.py`:
  - consume outbox events from Redpanda
  - dedupe via inbox table
  - write view + optional Redis cache
  - checkpoint updates.
- [x] Add `src/solution3/workers/rebuilder.py` command:
  - support `--from-beginning` mode.
- [x] Add `src/solution3/workers/reconciler.py`:
  - [x] scan stale `RESERVED` tasks and expire them after the TB timeout window
  - [x] align explicit TB posted/voided drift branches
  - [x] emit `tasks.expired` correction events and Redis hot-path updates.
- [x] Add `src/solution3/workers/webhook_dispatcher.py`:
  - consume terminal events
  - retry policy and exponential backoff
  - dead-letter to separate Postgres structure.
- [x] Add integration test for projector catch-up and query-view fallback under Redis cache loss.
- [x] Add integration test for Redpanda replay rebuild after projection reset.
- [x] Add integration test for stale reserved expiry with the worker intentionally stopped.
- [x] Add integration tests for webhook delivery success and dead-letter capture after bounded retries.
- [x] Add integration test for reconciler drift fix.

Completion criteria:

- [x] Poll can be served from query view under steady state.
- [x] Stale reserved tasks are corrected without manual intervention.
- [x] TigerBeetle terminal state drift is repaired back into command, query, and cache state without manual intervention.
