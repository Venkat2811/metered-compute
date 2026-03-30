# P0-011: Projector — query view materializer

Priority: P0
Status: done
Depends on: P1-006, P1-008

## Objective

Replace the `time.sleep(3600)` stub in `workers/projector.py` with a RabbitMQ consumer that materializes command-side events into the `query.task_query_view` table and Redis cache.

## Why

Without the projector, the query view is never populated. Poll currently falls back to stale Sol 1 patterns. The projector is the "Q" half of CQRS — it makes command-side writes visible to read queries.

## Scope

- `src/solution2/workers/projector.py`
  - RabbitMQ consumer subscribing to projector-specific queue (bound to `tasks` exchange)
  - Consumes events: `task.submitted`, `task.completed`, `task.failed`, `task.cancelled`
  - Inbox dedup: check `cmd.inbox_events` before processing
  - On each event:
    1. UPSERT `query.task_query_view` with current state (status, result, timestamps)
    2. Insert `cmd.inbox_events` for dedup
    3. Update Redis `task:{id}` hash with latest state
    4. Ack message
  - Idempotent: re-processing same event produces same view state
  - Graceful shutdown on SIGTERM/SIGINT
  - Prometheus metrics: events_projected_total, projection_lag_seconds
  - Structured logging

## Key design decisions

- Projector is append-only from event perspective — it never modifies command tables
- Event ordering: RabbitMQ per-queue FIFO is sufficient since each task_id routes to same queue
- Projection lag metric: `now() - event.created_at`

## Checklist

- [x] Consumer connects and subscribes to projector queue
- [x] Inbox dedup prevents double-projection
- [x] UPSERT into query.task_query_view for each event type
- [x] Redis task:{id} cache updated post-projection
- [x] Idempotent: same event projected twice = same result
- [x] SIGTERM graceful shutdown
- [x] Metrics exported

## Validation

- `uv run pytest tests/unit/test_projector.py -q`
- `uv run pytest tests/integration/test_projection_flow.py -q`
- Manual: submit → execute → poll shows COMPLETED via query view

## Acceptance Criteria

- Query view reflects all terminal states within projection lag SLA
- Redis cache is consistent with query view
- No duplicate rows in query view on redelivery
