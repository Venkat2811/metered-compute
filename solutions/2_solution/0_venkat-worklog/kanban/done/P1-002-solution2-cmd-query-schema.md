# P1-002: Solution2 CQRS DB Migration Baseline

Priority: P1
Status: done
Depends on: P1-001

## Objective

Introduce command/query schemas and tables required by the Sol2 workflow: task commands, reservations, outbox/inbox, and read model view.

## Scope

- Add migrations:
  - `0012_cmd_task_commands.sql`
  - `0013_cmd_credit_reservations.sql`
  - `0014_cmd_outbox_events.sql`
  - `0015_cmd_inbox_events.sql`
  - `0016_query_task_view.sql`
  - `0017_seed_users_sol2.sql`
- Migration verification script/tests for ordering and presence.
- Seed parity with Sol1 users/api keys.

## Checklist

- [x] Migration files include required columns, indexes, and constraints from `2_solution/tasks.md`.
- [x] Migration order is monotonic and deterministic from `0001`–`0017`.
- [x] Seed migration is idempotent (ON CONFLICT behavior).
- [x] `db.migrate` applies against fresh Postgres schema.
- [x] Index coverage for:
  - `cmd.task_commands`
  - `cmd.credit_reservations`
  - `cmd.outbox_events`
  - `query.task_query_view`

## Acceptance Criteria

- `cmd` and `query` schemas exist after migrate.
- A seeded user/API-key pair query passes smoke checks.
- Re-running migrations is idempotent where defined (`seed_users` and non-destructive constraints).

## Validation

- `pytest tests/unit/test_migrations.py -q`
- `docker compose up -d postgres && uv run python -m solution2.db.migrate`
- `psql ... -c "\\dt cmd.*"` and `\\dt query.*` for table presence
- `pytest tests/unit/test_migrations.py tests/unit/test_repository_cmd.py -q` (when added)
