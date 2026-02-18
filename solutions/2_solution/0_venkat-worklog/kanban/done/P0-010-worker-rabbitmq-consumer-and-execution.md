# P0-010: Worker — RabbitMQ consumer and task execution

Priority: P0
Status: done
Depends on: P1-006, P1-008

## Objective

Replace the `time.sleep(3600)` stub in `workers/worker.py` with a real RabbitMQ consumer that executes tasks, captures/releases reservations, updates query cache, and emits webhook events.

## Why

The worker is the core execution engine. Without it, submitted tasks never progress past PENDING. This is the single largest gap in Sol 2.

## Scope

- `src/solution2/workers/worker.py`
  - RabbitMQ consumer subscribing to `queue.realtime`, `queue.fast`, `queue.batch` (SLA-routed queues)
  - Inbox dedup: check `cmd.inbox_events` to skip already-processed messages (at-least-once → exactly-once)
  - On message received:
    1. Parse task command payload from message
    2. Simulate execution (sleep for model-class duration: small=2s, medium=4s, large=7s)
    3. On success: single PG transaction —
       - Update `cmd.task_commands` status → COMPLETED, set result blob
       - Call `capture_reservation()` (RESERVED → CAPTURED)
       - Insert `cmd.inbox_events` for dedup
       - Insert `cmd.outbox_events` with routing_key `task.completed` (for projector + webhook)
    4. On failure: single PG transaction —
       - Update `cmd.task_commands` status → FAILED
       - Call `release_reservation()` (RESERVED → RELEASED, refund `users.credits`)
       - Insert credit_transactions refund row
       - Insert `cmd.outbox_events` with routing_key `task.failed`
    5. Post-commit: update Redis `task:{id}` cache with terminal state
    6. Ack RabbitMQ message only after PG commit
  - Graceful shutdown on SIGTERM/SIGINT (drain current task, stop consuming)
  - Prometheus metrics: tasks_executed_total, task_duration_seconds, task_failures_total
  - Structured logging with task_id, user_id, model_class context

## Key repository functions to use

- `capture_reservation(pool, reservation_id)` — already exists in repository.py
- `release_reservation(pool, reservation_id)` — already exists in repository.py
- `create_outbox_event(conn, ...)` — already exists
- `create_inbox_event(conn, ...)` — already exists

## Checklist

- [x] Consumer connects to RabbitMQ and binds to all 3 SLA queues
- [x] Inbox dedup prevents double-execution on redelivery
- [x] Success path: task COMPLETED + reservation CAPTURED in single txn
- [x] Failure path: task FAILED + reservation RELEASED + credit refund in single txn
- [x] Post-commit Redis cache update
- [x] Outbox event emitted for projector/webhook consumption
- [x] SIGTERM drains in-flight task before exit
- [x] Metrics exported on configured port
- [x] Readiness probe available

## Validation

- `uv run pytest tests/unit/test_worker.py -q`
- `uv run pytest tests/integration/test_worker_flow.py -q` (compose required)
- Manual: submit task via API, verify it reaches COMPLETED in poll

## Acceptance Criteria

- Tasks progress from PENDING → RUNNING → COMPLETED/FAILED
- Reservation state transitions are atomic with task state
- Credit refund on failure is immediate and auditable
- No message loss on worker restart (unacked messages redelivered)
