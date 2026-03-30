# P0-012: Watchdog — reservation expiry and cleanup

Priority: P0
Status: done
Depends on: P1-008

## Objective

Replace the `time.sleep(3600)` stub in `workers/watchdog.py` with a periodic job that expires stale reservations, releases credits, and emits compensation events.

## Why

Without the watchdog, tasks stuck in PENDING/RUNNING with RESERVED credits will leak credits forever. The watchdog is the safety net that ensures the reservation billing model converges even when workers crash.

## Scope

- `src/solution2/workers/watchdog.py`
  - Periodic loop (configurable interval, default 30s)
  - Phase 1 — Expire stale reservations:
    1. SELECT reservations WHERE state=RESERVED AND created_at < now() - reservation_ttl
    2. For each: single PG transaction —
       - `release_reservation()` (RESERVED → RELEASED, refund users.credits)
       - Update `cmd.task_commands` status → TIMED_OUT
       - Insert credit_transactions refund row
       - Insert `cmd.outbox_events` with routing_key `task.timed_out`
    3. Post-commit: update Redis `task:{id}` cache
  - Phase 2 — Bulk expire terminal Redis results:
    1. Scan Redis for `task:{id}` keys with terminal status older than result_ttl
    2. DEL expired keys (PG is source of truth for historical queries)
  - Graceful shutdown on SIGTERM/SIGINT
  - Prometheus metrics: reservations_expired_total, credits_released_total, redis_keys_cleaned_total
  - Structured logging with batch counts per cycle

## Key repository functions to use

- `release_reservation(pool, reservation_id)` — already exists
- `create_outbox_event(conn, ...)` — already exists
- New: `list_expired_reservations(pool, ttl_seconds, limit)` — needs to be added

## Checklist

- [x] Periodic loop with configurable interval
- [x] Expired reservations detected and released
- [x] Credit refund atomic with reservation state change
- [x] Outbox event emitted for projector consumption
- [x] Terminal Redis keys cleaned after TTL
- [x] SIGTERM graceful shutdown
- [x] Metrics exported
- [x] No double-release (idempotent on RESERVED state check)

## Validation

- `uv run pytest tests/unit/test_watchdog.py -q`
- `uv run pytest tests/integration/test_watchdog_expiry.py -q`
- Manual: submit task, kill worker, wait for watchdog cycle, verify credits returned

## Acceptance Criteria

- No credit leak: every RESERVED reservation eventually reaches CAPTURED or RELEASED
- Expired tasks visible as TIMED_OUT in poll
- Watchdog is idempotent — running twice on same state produces no extra side effects
