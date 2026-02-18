# P1-032: PG-Durable JTI Revocation Blacklist

Priority: P1
Status: done
Depends on: P1-031

## Objective

Implement RFC-0001 revocation durability model: Postgres day-partitioned JTI blacklist as source-of-truth, Redis day buckets as hot cache, with PG fallback and startup rehydration.

## Checklist

- [x] Add migration `0009_token_revocations.sql`:
  - [x] create `token_revocations` parent table partitioned by `revoked_at`
  - [x] columns: `jti TEXT`, `user_id UUID`, `revoked_at TIMESTAMPTZ`, `expires_at TIMESTAMPTZ`
  - [x] primary key `(jti, revoked_at)`
  - [x] index `idx_token_revocations_user (user_id, revoked_at)`
  - [x] enable/configure `pg_partman` (`1 day` interval, premake `2`, retention `2 days`, `retention_keep_table=false`)
- [x] Ensure Postgres runtime includes `pg_partman` package in compose stack

- [x] Repository layer (`src/solution1/db/repository.py`):
  - [x] `insert_revoked_jti(executor, *, jti, user_id, expires_at)`
  - [x] `is_jti_revoked(pool, *, jti)`
  - [x] `load_active_revoked_jtis(pool, *, since)` returns `(jti, user_id, day_iso)`

- [x] Revocation service path (`src/solution1/services/auth.py`):
  - [x] add `revoke_jti(...): Redis SADD+EXPIRE then PG insert`

- [x] Revocation API endpoint (`src/solution1/app.py`):
  - [x] add `POST /v1/auth/revoke` (authenticated)
  - [x] extract `jti` + `exp` from verified JWT claims
  - [x] call `revoke_jti`
  - [x] return `{"revoked": true}`

- [x] Auth revocation check fallback (`src/solution1/app.py`):
  - [x] if Redis revocation check errors, fallback to PG `is_jti_revoked`
  - [x] write-through JTI back to Redis bucket on PG hit

- [x] Startup rehydration (`src/solution1/app.py`):
  - [x] load active revoked JTIs from PG since yesterday
  - [x] repopulate Redis day buckets (`SADD` + `EXPIRE`)
  - [x] log `revocation_rehydrated` count

- [x] Metrics/observability:
  - [x] add `token_revocations_total`
  - [x] add `revocation_pg_fallback_total`
  - [x] add `revocation_check_duration_seconds{source=redis|postgres}`

- [x] Tests:
  - [x] unit: repository revocation functions
  - [x] unit: `revoke_jti` dual-write behavior
  - [x] unit: `_is_token_revoked` PG fallback when Redis errors
  - [x] integration: `POST /v1/auth/revoke` then same token gets `401`
  - [x] integration: update existing revoked-token test to use API endpoint (no redis-cli shortcut)
  - [x] fault: revocation survives Redis restart path (PG fallback + startup rehydration)

- [x] End-to-end validation:
  - [x] run focused lint/type/unit/integration/fault suites for changed scope
  - [x] run `make prove` from clean state

## Acceptance Criteria

- [x] Revocation durability is PG-backed with partition lifecycle managed by `pg_partman`
- [x] Auth remains zero-DB on hot path when Redis is healthy
- [x] Redis outage uses PG fallback for revocation checks
- [x] Startup rehydrates Redis revocation cache from PG
- [x] All tests and `make prove` pass

## Validation Evidence

- Focused suites passed: unit/integration/fault revocation scope.
- Clean-state full gate passed: `make prove`.
- Compose readiness green after rebuild with pg_partman-enabled Postgres image.
