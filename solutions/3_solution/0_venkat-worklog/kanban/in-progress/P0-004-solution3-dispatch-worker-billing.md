# P0-004 Solution 3 - TB Billing, Relay, and Worker Dispatch

Objective:

Implement idempotent command handoff from PG command store into Redpanda and RabbitMQ worker dispatch with hot/cold routing.

Status: in progress as of 2026-03-26. Billing wrapper, relay seam, dispatcher publish contract, and worker completion seam are green; real Redpanda/RabbitMQ loops and end-to-end flow remain.

Acceptance criteria:

- [ ] TigerBeetle reserve path for submit and idempotent retry safety.
- [ ] Outbox relay publishes command events exactly once from command row state.
- [ ] Dispatcher reads Redpanda and publishes queue tasks to RabbitMQ with headers.
- [ ] Worker consumes queue and updates command state and outbox completion safely.

TDD order:

1. Add unit tests for TigerBeetle account mapping, transfer lifecycle, and relay idempotency.
2. Add integration tests with mock RabbitMQ/Redpanda adapters for dispatch semantics.
3. Implement service-by-service with contract-first interfaces.

Checklist:

- [x] Billing service:
  - add `src/solution3/services/billing.py`.
  - implement `reserve_credits`, `post_pending_transfer`, `void_pending_transfer`.
  - include account bootstrap and user-account mapping table helper.
- [ ] Outbox relay:
  - add `src/solution3/workers/outbox_relay.py`.
  - read `cmd.outbox_events` and publish to Redpanda topics.
  - mark publish success/failure with retry.
- [ ] Dispatcher:
  - add `src/solution3/workers/dispatcher.py`.
  - consume task events and publish `queue.fast` vs `queue.slow`.
  - use RabbitMQ headers for model class and tier routing.
- [ ] Worker runtime:
  - add `src/solution3/workers/worker.py`.
  - implement cold-start model cache and active worker tracking.
  - execute compute path and push terminal completion event to outbox.
- [ ] Add command completion flow:
  - post TB completion transfer on success.
  - void TB pending on failure/timeout/cancel.
- [ ] Add end-to-end integration tests:
  - submit -> relay -> dispatch -> worker -> poll completed.

Completion criteria:

- [ ] Worker failures do not leak pending TigerBeetle transfers.
- [ ] Dispatch path is stable under repeated delivery / duplicate events.

Sub-slices complete so far:

- [x] TigerBeetle billing primitives with unit coverage.
- [x] Outbox relay publish/flush/mark ordering seam with unit coverage.
- [x] Dispatcher topology + durable publish contract with unit coverage.
- [x] Worker running/completion guard seam with TigerBeetle post/void and Redis cache updates.
