# P1-005: Solution2 Submit Path with Reservation Billing

Priority: P1
Status: done
Depends on: P1-004

## Objective

Implement `POST /v1/task` as a single PG transaction: reserve credits, persist command, emit outbox event, and write query cache after commit.

## Scope

- Repository layer for cmd tables:
  - idempotent command insert keyed by `(user_id, idempotency_key)`
  - reservation creation and guardrails
  - `create_task_command`, `create_reservation`, `create_outbox_event`
- Billing orchestration service:
  - concurrency limit check using active reservations
  - insufficient credits -> 402
  - concurrency limit -> 429
  - idempotent replay semantics
- API mapping and enqueue contract (`queue` / routing) via `resolve_queue`.

## Checklist

- [x] PG transaction includes idempotency check + reservation + task command + outbox row in `run_admission_gate`.
- [x] Redis task-state write is post-admission.
- [x] Cost uses model multiplier policy.
- [x] Conflict response returned for idempotency collisions.
- [x] Command replay validation implemented for cached idempotent submissions.

## Acceptance Criteria

- Submit endpoint is side-effect atomic on DB and does not emit false outbox on failures.
- Terminal failure cases return correct status/errors and leave reservation/cmd state consistent.

## Validation

- `pytest tests/unit/test_app_paths.py::test_submit_idempotent_conflict_and_replay tests/unit/test_app_paths.py::test_submit_accept_path_and_hit_endpoint tests/unit/test_app_paths.py::test_submit_includes_trace_context_in_stream_payload tests/unit/test_app_paths.py::test_submit_persists_failures_are_compensated tests/fault/test_publish_failure_path.py::test_submit_returns_503_on_task_persist_failure`
