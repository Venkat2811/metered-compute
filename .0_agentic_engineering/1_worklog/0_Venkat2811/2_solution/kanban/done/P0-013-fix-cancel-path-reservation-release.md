# P0-013: Fix cancel path — reservation release and PG-native refund

Priority: P0
Status: done
Depends on: P1-008

## Objective

Rewrite the cancel path to use Sol 2 reservation billing: release reservation + PG credit refund + outbox event, removing all Sol 1 Redis compensation patterns.

## Why

Current cancel path is architecturally wrong:
1. `update_task_cancelled()` updates the old `tasks` table instead of `cmd.task_commands`
2. No `release_reservation()` call — credits stay RESERVED forever
3. Redis compensation via `refund_and_decrement_active()` uses Sol 1 patterns (credits:{uid}, active:{uid})
4. `pending_marker_key` deletion is Sol 1 pattern

## Current (broken)

```
_apply_cancel_transaction:
  → UPDATE tasks SET status='CANCELLED'          # wrong table
  → INSERT credit_transactions                    # correct idea, wrong context
_sync_cancel_state_to_redis:
  → refund_and_decrement_active()                 # Sol 1 Redis compensation
  → DEL pending_marker_key                        # Sol 1 pattern
```

## Target (per RFC-0002)

```
cancel_task:
  1. BEGIN
  2. SELECT reservation FROM cmd.credit_reservations
     WHERE task_id = $1 AND state = 'RESERVED' FOR UPDATE
  3. UPDATE cmd.credit_reservations SET state = 'RELEASED'
  4. UPDATE users SET credits = credits + reservation.amount
  5. UPDATE cmd.task_commands SET status = 'CANCELLED'
  6. INSERT cmd.credit_transactions (refund)
  7. INSERT cmd.outbox_events (task.cancelled)
  8. COMMIT
  9. Post-commit: UPDATE Redis task:{id} cache
```

## Scope

- `src/solution2/api/task_write_routes.py`
  - Rewrite `_apply_cancel_transaction()` to use cmd.task_commands + release_reservation
  - Rewrite `_sync_cancel_state_to_redis()` to only update `task:{id}` cache (no Sol 1 keys)
  - Remove `refund_and_decrement_active()` call
  - Remove `pending_marker_key` usage
- `src/solution2/db/repository.py`
  - Fix or replace `update_task_cancelled()` to target `cmd.task_commands`
  - Add `get_reservation_for_task(conn, task_id)` if not exists
  - Ensure `release_reservation()` does credit refund atomically

## Checklist

- [x] Cancel updates cmd.task_commands (not old tasks table)
- [x] Reservation released (RESERVED → RELEASED) with credit refund
- [x] Credit transaction row inserted for audit trail
- [x] Outbox event emitted (task.cancelled) for projector
- [x] Redis task:{id} cache updated post-commit
- [x] No Sol 1 Redis key usage (no credits:{uid}, active:{uid}, pending_marker)
- [x] Idempotent: cancelling already-cancelled task returns success
- [x] Cannot cancel terminal tasks (COMPLETED, FAILED, TIMED_OUT)

## Validation

- `uv run pytest tests/unit/test_cancel_path.py -q`
- `uv run pytest tests/integration/test_cancel_flow.py -q`
- Manual: submit → cancel → poll shows CANCELLED + credits refunded

## Acceptance Criteria

- Cancel is a single PG transaction (no distributed compensation)
- Credits are refunded in PG (source of truth), not Redis
- Outbox event triggers projector update of query view
