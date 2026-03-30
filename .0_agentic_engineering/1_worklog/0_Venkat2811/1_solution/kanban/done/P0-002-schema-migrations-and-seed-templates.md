# P0-002: Schema Migrations and Seed Templates

Priority: P0
Status: done
Depends on: P0-001

## Objective

Implement Postgres control-plane schema for Solution 1: hashed API keys, credit audit/snapshots, drift audit, and migration template rendering.

## Checklist

- [x] Create migrations for `users`, `api_keys`, `credit_transactions`, `credit_snapshots`, `credit_drift_audit`, `stream_checkpoints`
- [x] Use migration templating for reproducible dev defaults (keys, roles, tiers, status values)
- [x] Add indexes from RFC-0001 (`api_keys`, `credit_transactions`, `credit_drift_audit`)
- [x] Implement migration runner with `schema_migrations` tracking
- [x] Add seed data path with deterministic local defaults

## Acceptance Criteria

- [x] Fresh database migration + re-run migration both succeed
- [x] Seed data is idempotent and environment-driven
- [x] Schema/indexes match RFC-0001 storage section

## Progress Notes (2026-02-16)

Completed:

- Added `0006_solution1_control_plane_tables.sql` migration introducing:
  - `users.tier` and `users.is_active`
  - `api_keys` (SHA-256 key hash + prefix + role/tier flags)
  - `credit_drift_audit`
  - `stream_checkpoints`
  - indexes `idx_api_keys_user_active` and `idx_drift_checked`
- Extended migration template values with tier placeholders (`DEFAULT_TIER`, `ADMIN_TIER`, `TIER_VALUES_SQL`).
- Added `SubscriptionTier` constants and SQL value helpers in `constants.py`.
- Extended migration unit tests to lock migration ordering and template rendering for control-plane schema.

Verification commands:

- `pytest tests/unit/test_migrations.py -q`
- `make test-unit`
- `make lint type`

Notes:

- Schema is evolved compatibly from the copied baseline so subsequent cards can replace runtime paths incrementally without breaking migration chain.
