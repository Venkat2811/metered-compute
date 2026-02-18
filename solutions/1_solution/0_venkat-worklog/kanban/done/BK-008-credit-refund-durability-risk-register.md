# BK-008: Credit Refund Durability Risk Register

Priority: P1
Status: done
Depends on: P1-021

## Objective

Track credit-refund durability failures and residual risk across API admission, worker failure, and reaper repair paths as an explicit and reviewable register.
Keep scope to risk identification, monitoring, and low-risk guardrails that reduce over-refund exposure without behavior changes.

## Checklist

- [x] Document known limitation and blast radius in RFC/runbook
- [x] Define trigger/threshold for promoting this into active implementation scope
- [x] Add mitigation monitoring signals (error count/backlog indicators)
- [x] Add no-refund regression tests for no partial-compensation edges

## Acceptance Criteria

- [x] Limitation is explicit, bounded, and reviewable
- [x] Promotion criteria to active implementation are clear
- [x] Risks mapped to exact code paths, residual risk called out, and monitoring thresholds documented
- [x] Tests cover no-refund paths so compensation only occurs after durable DB-side transition+ledger success

## Findings and Remediations

- [R1] API persist failure before/after DB write (high)
  - Severity: High (temporary overcharge + active-slot leakage).
  - Evidence: `src/solution1/api/task_write_routes.py` persists task row + `task_deduct` before confirming Redis cleanup.
  - Mitigation:
    - Keep compensation path explicit to Redis and idempotency cleanup only when DB transaction fails.
    - Mark `db_row_created` and only run compensation when ledger/task row are not known to have been committed.
  - Residual risk: process crash window between DB commit and Redis cleanup can still delay repair.
  - Files:
    - `src/solution1/api/task_write_routes.py`
    - `tests/unit/test_app_paths.py`

- [R2] Worker failure after task terminal DB transition before Redis refund (medium)
  - Severity: Medium (temporary overcharge if process crashes after DB success).
  - Evidence: `_handle_failure` in `src/solution1/workers/stream_worker.py`.
  - Mitigation:
    - `refund_and_decrement_active` now gates on both DB ops succeeding in one transaction.
    - Add explicit log branch `stream_task_failure_db_update_failed`.
  - Residual risk: crash before Redis refund means temporary credit debt until recovery job or manual fix.
  - Files:
    - `src/solution1/workers/stream_worker.py`
    - `tests/unit/test_stream_worker.py`

- [R3] Reaper stuck-task refund/write mismatch (high)
  - Severity: High (retry semantics can skip refund in uncertain states).
  - Evidence: `_process_stuck_tasks` in `src/solution1/workers/reaper.py`.
  - Mitigation:
    - Require `update_task_failed` + `insert_credit_transaction` success before `refund_and_decrement_active`.
    - Add no-refund branch for DB failure cases.
  - Residual risk: DB-level failure can suppress repair before the next reconciliation tick.
  - Files:
    - `src/solution1/workers/reaper.py`
    - `tests/unit/test_reaper_recovery.py`

## Promotion criteria

- Any single condition for 30 minutes:
  - `sum(rate(task_submissions_total{result="persist_failure"}[5m])) / sum(rate(task_submissions_total[5m])) > 0.1`
  - log-level `stream_task_failure_db_update_failed` or `reaper_stuck_task_refund_error` rate > 3/min
  - `sum(rate(reaper_refunds_total{reason="stuck_task"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`
  - `sum(rate(reaper_refunds_total{reason="orphan_marker"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`

## Validation

- `uv run ruff check src/solution1/api/task_write_routes.py src/solution1/workers/stream_worker.py src/solution1/workers/reaper.py tests/unit/test_app_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_stream_worker.py`
- `uv run pytest -q tests/unit/test_app_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_stream_worker.py`

## Notes

- RFC section updated: `0_1_rfcs/RFC-0001-1-solution-redis-native-engine/data-ownership.md`
- Runbook section added for BK-008 monitoring and trigger thresholds: `worklog/RUNBOOK.md`
