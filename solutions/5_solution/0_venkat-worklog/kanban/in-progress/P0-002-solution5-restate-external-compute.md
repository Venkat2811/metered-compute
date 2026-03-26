# P0-002 Solution 5 - Restate-Control Plane + External Compute Separation

Objective:

Refactor inline workflow compute into an external compute plane while keeping Restate as durable orchestration layer.

Acceptance criteria:

- [x] Workflow invokes an external compute worker instead of doing inline arithmetic.
- [x] Control plane stores terminal state only after compute result receipt.
- [x] Cancellation and timeout semantics are explicit and idempotent.

TDD order:

1. Add unit tests for workflow orchestration helpers and result waiting behavior.
2. Add integration test that exercises submit -> compute -> complete with mocked worker handoff.
3. Add failure-path tests for timeout and cancellation before implementing retry/backoff.

Checklist:

- [x] Create/extend compute gateway module:
  - `src/solution5/workers/compute_gateway.py` (or `services/compute.py`).
  - push request payload with task_id, user_id, model metadata.
- [x] Add lightweight compute worker process:
  - `src/solution5/workers/compute_worker.py`.
  - return result via Redis queue or Restate ingress endpoint.
- [x] Update Restate workflow in `src/solution5/workflows.py`:
  - set `PENDING`/`RUNNING` transitions before dispatch.
  - await result with timeout handling (heartbeat updates remain deferred).
  - handle cancel signal and map to safe TB void/cancel path.
- [x] Ensure idempotency of duplicate callbacks/results.
- [x] Add result-ack path back to repository and Redis caches.
- [x] Expand tests for canceled/timeout races with simulated slow worker.

Completion criteria:

- [x] Inline `x+y` no longer executes in the workflow directly.
- [x] External worker faults are surfaced as deterministic workflow outcomes.
