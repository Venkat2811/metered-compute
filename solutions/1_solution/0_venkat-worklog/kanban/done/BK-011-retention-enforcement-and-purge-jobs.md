# BK-011: Retention Enforcement and Purge Jobs

Priority: P1
Status: done
Depends on: P1-021

## Summary

Added bounded reaper retention for historical credit records to reduce unbounded table growth and align with operational risk management in solution1.

## Implemented

- Configurable reaper settings added in `src/solution1/core/settings.py` + `.env.dev.defaults`:
  - `REAPER_RETENTION_BATCH_SIZE`
  - `REAPER_CREDIT_TRANSACTION_RETENTION_SECONDS`
  - `REAPER_CREDIT_DRIFT_AUDIT_RETENTION_SECONDS`
- Added bounded purge repository helpers:
  - `purge_old_credit_transactions(...)`
  - `purge_old_credit_drift_audit(...)`
  - both delete by bounded `ORDER BY ... LIMIT batch_size` with timestamp cutoff.
- Reaper cleanup execution added in `src/solution1/workers/reaper.py`:
  - Purges only when retention window is enabled (`> 0`) and records counters.
  - Cycle log includes purge counts.
- Observability:
  - `REAPER_RETENTION_DELETES_TOTAL` metric added in `src/solution1/observability/metrics.py`
  - Contract test updated to include new metric symbol.
- DB index support:
  - Added migration `src/solution1/db/migrations/0007_reaper_retention_indexes.sql`
  - Updated `test_migrations.py` ordered file assertion.

## Acceptance status

- [x] Define purge cadence and safety bounds (`REAPER_RETENTION_BATCH_SIZE`, bounded batch deletes).
- [x] Add index and observability considerations (new metric, bounded delete index migration).
- [x] Implement tests for bounded purge behavior (new unit tests in `tests/unit/test_reaper_retention.py` and reaper cycle wiring tests).

## Evidence and validation notes

- Runbook:
  - `worklog/RUNBOOK.md` updated with BK-011 operational checks and controls.
- Retention settings now documented in `README.md`.

## Residual risk / next steps

- `stream_checkpoints` remains intentionally untouched in this card since no runtime read path exists yet; purging it would create recoverability risk without explicit replay semantics.
- Current retention windows default to 24h for demos and are intended to be raised for production based on storage/SLA policy.
