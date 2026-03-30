# P1-008: Solution2 Command/Query repository surface

Priority: P1
Status: done
Depends on: P1-007

## Objective

Create a Sol2-safe repository API for `cmd.*` and `query.*` storage, while keeping Sol1 auth/user helpers available where needed.

## Scope

- Refactor `src/solution2/db/repository.py`:
  - Add/keep command APIs for task commands and reservations.
  - Add/keep query APIs for task query view upsert/fetch.
  - Add outbox/inbox API operations.
  - Keep user/auth APIs for backward-compatibility with API token path.
- Add/adjust migration-backed SQL tests for:
  - `create_task_command`, `get_task_command`, `update_task_command_status`
  - `create_reservation`, `capture_reservation`, `release_reservation`
  - `count_active_reservations`, `find_expired_reservations`
  - `create/fetch/mark_outbox_event`, `check/record_inbox_event`
  - `upsert_task_query_view`, `get_task_query_view`, `bulk_expire_results`

## Checklist

- [x] Separate command/query SQL operations from stream checkpoint helpers.
- [x] Add strict reservation-state persistence transitions (reserve/capture/release filters).
- [x] Keep function names stable where Sol1 callers still exist (compatibility paths untouched).
- [x] Add tests for all new/ported repository functions.

## Validation

- `uv run pytest -q tests/unit/test_repository_cmd_query.py`
- `uv run pytest -q tests/unit` (all 2_solution repository tests currently passing)

## Acceptance Criteria

- Reservation lifecycle transitions are enforced at persistence boundaries.
- CQRS query view and command tables can be read independently without stream references.
