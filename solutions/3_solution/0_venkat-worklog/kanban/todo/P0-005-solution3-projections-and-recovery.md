# P0-005 Solution 3 - Projections, Reconciler, and Webhook Worker

Objective:

Add query-side materialization and recovery mechanisms so Sol 3 is operational under stale states and infra churn.

Acceptance criteria:

- [ ] Projector consumes command events into query view and checkpoints offsets.
- [ ] Rebuilder mode can replay from topic start and restore query view.
- [ ] Reconciler resolves stale reserved states and pending terminal drifts.
- [ ] Webhook worker dispatches callbacks with retry/dead-letter policy.

TDD order:

1. Add projector unit tests around idempotent consumption and checkpoint progression.
2. Add reconciler tests with simulated stale transfer states.
3. Add webhook dispatch tests for retry and success path.
4. Implement services incrementally from projector upward.

Checklist:

- [ ] Add `src/solution3/db/repository.py` query-side methods:
  - upsert into `query.task_query_view`
  - checkpoint reads/writes
  - projection audit helpers
- [ ] Add `src/solution3/workers/projector.py`:
  - consume outbox events from Redpanda
  - dedupe via inbox table
  - write view + optional Redis cache
  - checkpoint updates.
- [ ] Add `src/solution3/workers/rebuilder.py` command:
  - support `--from-beginning` mode.
- [ ] Add `src/solution3/workers/reconciler.py`:
  - scan stale `RESERVED` tasks
  - consult TB transfer status
  - emit correction events.
- [ ] Add `src/solution3/workers/webhook_worker.py`:
  - consume terminal events
  - retry policy and exponential backoff
  - dead-letter to separate structure.
- [ ] Add integration test for projector catch-up and reconciler drift fix.

Completion criteria:

- [ ] Poll can be served from query view under steady state.
- [ ] Stale reserved tasks are corrected without manual intervention.
