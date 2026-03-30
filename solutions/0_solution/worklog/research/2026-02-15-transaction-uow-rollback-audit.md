# 2026-02-15 Transaction UoW and Rollback Audit (Solution 0)

Scope:

- API submit/cancel/admin paths
- worker failure path
- reaper recovery path

## Unit-of-work boundaries

Submit path:

- Redis Lua admission (atomic in Redis): credit check/deduct + concurrency + idempotency
- Postgres transaction:
  - insert `tasks`
  - insert `credit_transactions` (`task_deduct`)
- Publish failure compensation:
  - Redis refund + active decrement
  - Postgres transaction: mark task failed + insert refund txn

Cancel path:

- Postgres transaction:
  - update `tasks` -> `CANCELLED`
  - insert `credit_transactions` (`cancel_refund`)
- Redis compensation: refund + active decrement

Admin credit path:

- single-statement CTE update + audit insert in Postgres
- best-effort Redis cache sync/invalidate (non-critical write-through)

Worker terminal failure:

- Postgres transaction:
  - mark task failed
  - insert `credit_transactions` (`failure_refund`)
- Redis refund + active decrement

Reaper recovery:

- Detect orphan/stuck
- Postgres transaction + Redis compensation per recovery type

## Rollback and compensation tests

Representative tests:

- `tests/unit/test_app_paths.py::test_submit_returns_503_on_pool_exhaustion`
- `tests/fault/test_publish_failure_path.py`
- `tests/fault/test_runtime_faults.py`
- `tests/unit/test_reaper_recovery.py`
- `tests/unit/test_billing_service.py`

Assertions covered:

- No double-charge on failure paths
- Refund applied on publish failure/worker failure/cancel
- Service degrades with deterministic error responses
- Reaper converges orphan/stuck states

## Conclusion

For Solution 0 scope, all multi-step mutation paths are either:

- wrapped in explicit transaction boundaries, or
- paired with tested compensating actions.

Residual risk (explicitly accepted in RFC0): Redis+Celery dual-write window, mitigated by immediate compensation and reaper sweep.
