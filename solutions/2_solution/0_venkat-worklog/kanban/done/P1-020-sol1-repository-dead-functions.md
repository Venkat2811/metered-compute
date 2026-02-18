# P1-020: Remove Sol 1 repository dead functions

Priority: P1
Status: done
Depends on: P0-010, P0-013, P0-014

## Objective

Remove repository functions that target the old `tasks` table and Sol 1 patterns, keeping only cmd.*/query.* functions.

## Why

repository.py has both Sol 1 functions (`get_task`, `update_task_running`, `update_task_completed`, `update_task_failed`, `update_task_cancelled` targeting `tasks` table) and Sol 2 functions (`create_task_command`, `create_reservation`, `capture_reservation`, `release_reservation`). After P0 cards fix all callers, the Sol 1 functions are dead code.

## Scope

- `src/solution2/db/repository.py`
  - Delete functions that query/update old `tasks` table:
    - `get_task()` — replaced by query view lookup
    - `update_task_running()` — replaced by worker cmd update
    - `update_task_completed()` — replaced by worker cmd update
    - `update_task_failed()` — replaced by worker cmd update
    - `update_task_cancelled()` — replaced by P0-013 cmd update
  - Verify no callers remain after P0 cards
  - Clean imports

## Checklist

- [x] All old `tasks` table functions removed
- [x] No callers reference deleted functions
- [x] cmd.* and query.* functions are the only data access patterns
- [x] All tests pass
- [ ] `mypy --strict` passes (`src/tests` baseline still has pre-existing strict errors in `services/rabbitmq.py` + outbox relay tests)

## Validation

- `uv run pytest tests/ -q`
- `grep -r "update_task_running\|update_task_completed\|update_task_failed\|update_task_cancelled" src/solution2/` returns nothing (or only in migrations/seed)
