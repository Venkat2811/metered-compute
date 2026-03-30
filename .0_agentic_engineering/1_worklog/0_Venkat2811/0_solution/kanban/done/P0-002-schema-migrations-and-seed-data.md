# P0-002: Schema, Migrations, and Seed Data

Priority: P0
Status: done
Depends on: P0-001

## Objective

Implement the baseline data model from RFC with forward-only migrations, indexes, and spec-faithful seed users.

## Checklist

- [x] Add migration set for:
  - [x] `users` schema (`name`, `api_key`, `credits`)
  - [x] `tasks`
  - [x] `credit_transactions`
  - [x] `credit_snapshots`
- [x] Apply index strategy from RFC:
  - [x] unique idempotency index (partial)
  - [x] task status/time index
  - [x] task user/time index
  - [x] credit transaction user/time index
- [x] Add deterministic seed fixture with API keys
- [x] Add migration runner for local and test harness

## TDD Subtasks

1. Red

- [x] Add failing migration tests (empty DB -> target schema)
- [x] Add failing seed verification test for exact records

2. Green

- [x] Implement migrations and seed loader to pass tests

3. Refactor

- [x] Split schema DDL and seed DML for maintainability

## Acceptance Criteria

- [x] Fresh Postgres initializes to expected schema
- [x] Seed users match keys and balances
- [x] Query plans hit expected indexes for poll/history patterns

## Progress Notes (2026-02-15)

Implemented:

- migration runner and CLI:
  - `src/solution0/db/migrate.py`
- ordered SQL migration set:
  - `src/solution0/db/migrations/0001_create_users_base.sql`
  - `src/solution0/db/migrations/0002_extend_users_and_add_task_tables.sql`
  - `src/solution0/db/migrations/0003_indexes.sql`
  - `src/solution0/db/migrations/0004_seed_users.sql`
- migration-focused tests:
  - `tests/unit/test_migrations.py`

Evidence:

- red phase: `pytest tests/unit/test_migrations.py` failed with `ModuleNotFoundError: No module named 'solution0.db'`
- green phase: `pytest tests/unit/test_migrations.py` passed (`3 passed`)
- quality gate: `./scripts/ci_check.sh` passed (`14 passed`, ruff + mypy clean)
- compose runtime validation: `docker compose ps` showed healthy `postgres`, `redis`, and running `api`/`worker`/`reaper`
- index evidence:
  - `EXPLAIN ... WHERE user_id = ... ORDER BY created_at DESC LIMIT 10` used `idx_tasks_user_created`
  - `EXPLAIN ... WHERE status='PENDING' ORDER BY created_at` used `idx_tasks_status_created`
