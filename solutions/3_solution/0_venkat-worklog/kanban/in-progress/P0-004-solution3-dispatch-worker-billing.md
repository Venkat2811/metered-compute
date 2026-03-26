# P0-004 Solution 3 - TB Billing, Relay, and Worker Dispatch

Objective:

Implement idempotent command handoff from PG command store into Redpanda and RabbitMQ worker dispatch with hot/cold routing.

Status: in progress as of 2026-03-26. Billing wrapper, outbox-relay process, dispatcher bridge, RabbitMQ cold-queue worker consume loop, TigerBeetle reserve/post/void path, and end-to-end submit -> complete flow are green. Remaining gap in this slice is warm/preloaded routing.

Acceptance criteria:

- [x] TigerBeetle reserve path for submit and idempotent retry safety.
- [ ] Outbox relay publishes command events exactly once from command row state.
- [ ] Dispatcher reads Redpanda and publishes queue tasks to RabbitMQ with headers.
- [x] Worker consumes queue and updates command state safely.
- [ ] Dispatcher + worker prefer warm/preloaded queues before cold fallback.

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
- [x] Dispatcher:
  - add `src/solution3/workers/dispatcher.py`.
  - consume task events and publish RabbitMQ work messages with headers.
  - current implementation proves the cold-queue path; warm/preloaded routing remains.
  - use RabbitMQ headers for model class and tier routing.
- [x] Worker runtime:
  - add `src/solution3/workers/worker.py`.
  - implement cold-start model cache and active worker tracking.
  - execute compute path and finalize command state over the live RabbitMQ path.
- [x] Add command completion flow:
  - post TB completion transfer on success.
  - void TB pending on failure/timeout/cancel.
- [x] Add end-to-end integration tests:
  - submit -> relay -> dispatch -> worker -> poll completed.

Completion criteria:

- [x] Worker failures do not leak pending TigerBeetle transfers.
- [x] Dispatch path is stable under repeated delivery / duplicate events on the cold-queue path.
- [ ] Warm/preloaded routing is exercised and proven with dedicated tests.

Sub-slices complete so far:

- [x] TigerBeetle billing primitives with unit coverage.
- [x] Outbox relay publish/flush/mark ordering seam with unit coverage.
- [x] Outbox relay process with concrete Redpanda producer and strict unit coverage.
- [x] Dispatcher topology + durable publish contract with unit coverage.
- [x] Dispatcher process with concrete Redpanda consumer and RabbitMQ channel bridge coverage.
- [x] Worker running/completion guard seam with TigerBeetle post/void and Redis cache updates.
- [x] Worker model runtime seam with cold-start, warm-registry, and hot-path unit coverage.
- [x] Live integration proof for outbox-relay -> Redpanda -> dispatcher -> RabbitMQ cold-queue delivery.
- [x] Live integration proof for submit -> TB reserve -> relay -> Redpanda -> dispatcher -> RabbitMQ -> worker -> completed poll result.
