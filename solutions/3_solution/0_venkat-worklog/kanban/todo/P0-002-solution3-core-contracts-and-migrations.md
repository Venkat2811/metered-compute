# P0-002 Solution 3 - Core Contracts and Storage Model

Objective:

Define all shared domain contracts and schema primitives required by TigerBeetle + Redpanda + CQRS flow.

Acceptance criteria:

- [ ] Shared enums/constants compile and are used consistently in SQL migrations and runtime paths.
- [ ] Command/query schemas can be created from clean migration run.
- [ ] `migrate.py` supports template-based SQL rendering with enum-driven literals.

TDD order:

1. Add unit tests for constants/settings serialization and migration rendering templates.
2. Add SQL migration tests that validate rendered placeholders and constraint names.
3. Implement constants/models/repository query boundaries to satisfy tests.

Checklist:

- [ ] Add/extend `src/solution3/core/constants.py`:
  - `TaskStatus`, `ModelClass`, `SubscriptionTier`, `RequestMode`, routing constants.
  - `TASK_STATUSES`, `TASK_TERMINAL_STATUSES`, `TASK_CANCELLED_STATUSES`, `TASK_EVENT_TYPES`.
- [ ] Add/extend `src/solution3/core/settings.py`:
  - TigerBeetle cluster settings, redpanda settings, RabbitMQ settings, event-topic names, checkpoint timing.
- [ ] Add `src/solution3/db/migrations/0001_create_schema.sql` and 0002+ initial files with placeholders for:
  - `cmd.task_commands`
  - `cmd.task_events`
  - `cmd.outbox_events`
  - `cmd.inbox_events`
  - `query.task_query_view`
  - `cmd.projection_checkpoints`
  - `users`, `api_keys`, `reconcile_jobs` tables.
- [ ] Implement `src/solution3/db/migrations/0003_seed_users.sql` using RFC-0003 seed shape.
- [ ] Extend `src/solution3/db/migrate.py`:
  - template replacement for enum-driven constants
  - validation on migration filenames and transaction-safe runs.
- [ ] Add contract tests for `load_migration_sql()` and `render_migration_sql()`.
- [ ] Add migration runner smoke script under `scripts/migrate.sh`.

Completion criteria:

- [ ] `render_migration_sql()` outputs fully expanded constants for all 000x scripts.
- [ ] New tables created without manual SQL substitutions.
