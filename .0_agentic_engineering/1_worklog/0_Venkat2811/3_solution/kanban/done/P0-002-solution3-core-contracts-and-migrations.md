# P0-002 Solution 3 - Core Contracts and Storage Model

Objective:

Define all shared domain contracts and schema primitives required by TigerBeetle + Redpanda + CQRS flow.

Status: completed on 2026-03-26 after live migration proof against compose Postgres.

Acceptance criteria:

- [x] Shared enums/constants compile and are used consistently in SQL migrations and runtime paths.
- [x] Command/query schemas can be created from clean migration run.
- [x] `migrate.py` supports template-based SQL rendering with enum-driven literals.

TDD order:

1. Add unit tests for constants/settings serialization and migration rendering templates.
2. Add SQL migration tests that validate rendered placeholders and constraint names.
3. Implement constants/models/repository query boundaries to satisfy tests.

Checklist:

- [x] Add/extend shared constants in `src/solution3/constants.py`:
  - `TaskStatus`, `ModelClass`, `SubscriptionTier`, `RequestMode`, `BillingState`, routing constants.
  - SQL-literal helper constants, terminal/cancellable state groups, and `TASK_EVENT_TYPES`.
- [x] Add/extend `src/solution3/core/settings.py`:
  - TigerBeetle cluster settings, redpanda settings, RabbitMQ settings, event-topic names, checkpoint timing.
- [x] Add `src/solution3/db/migrations/0001_create_schemas.sql` and 0002+ initial files with placeholders for:
  - `cmd.task_commands`
  - `cmd.outbox_events`
  - `cmd.inbox_events`
  - `query.task_query_view`
  - `cmd.projection_checkpoints`
  - `cmd.users`, `cmd.api_keys`, and `cmd.billing_reconcile_jobs`.
- [x] Correct stale card assumption: RFC-0003 defines `cmd.outbox_events` and `cmd.inbox_events`, not `cmd.task_events`.
- [x] Implement `src/solution3/db/migrations/0003_seed_users.sql` using RFC-0003 seed shape.
- [x] Extend `src/solution3/db/migrate.py`:
  - template replacement for enum-driven constants
  - validation on migration filenames and transaction-safe runs.
- [x] Add contract tests for `load_migration_sql()` and `render_migration_sql()`.
- [x] Add migration runner smoke script under `scripts/migrate.sh`.

Completion criteria:

- [x] `render_migration_sql()` outputs fully expanded constants for all 000x scripts.
- [x] New tables created without manual SQL substitutions.

Verification notes:

- `pytest tests_bootstrap/unit` passed on 2026-03-26.
- `pytest tests_bootstrap/integration/test_migrations.py -m integration` passed on 2026-03-26.
- `make quality` passed on 2026-03-26.
- `make migrate` returned `No pending migrations.` after the integration proof.
