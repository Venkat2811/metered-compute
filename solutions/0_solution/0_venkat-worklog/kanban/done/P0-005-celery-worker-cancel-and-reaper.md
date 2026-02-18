# P0-005: Celery Worker, Cancel Flow, and Reaper

Priority: P0
Status: done
Depends on: P0-004

## Objective

Implement asynchronous worker execution lifecycle with safe cancellation, retries, and compensation recovery.

## Checklist

- [x] Wire Celery task publish from submit path
- [x] Worker lifecycle transitions (`PENDING -> RUNNING -> COMPLETED|FAILED|CANCELLED`)
- [x] Publish-failure compensation (`INCRBY` refund + active decrement)
- [x] Cancel path with revoke + refund + state update
- [x] Reaper job:
  - [x] orphan deduction recovery
  - [x] stuck task timeout recovery
  - [x] dirty credit snapshot flush
  - [x] result expiry cleanup

## TDD Subtasks

1. Red

- [x] Add failing integration tests for worker success/failure/retry outcomes
- [x] Add failing fault tests for crash-between-deduct-and-publish and stuck-running timeout

2. Green

- [x] Implement worker + reaper flows until tests pass

3. Refactor

- [x] Consolidate credit mutation logic in typed service to avoid divergent paths

## Acceptance Criteria

- [x] No leaked active counters after terminal paths
- [x] Refund behavior is exactly-once in all tested recovery scenarios
- [x] Reaper converges inconsistent states within bounded time

## Progress Notes (2026-02-15)

Implemented:

- Celery app and task execution:
  - `src/solution0/celery_app.py`
  - `src/solution0/worker_tasks.py`
- compensation and credit mutation paths:
  - `src/solution0/services/billing.py`
  - `src/solution0/app.py` (persist/publish failure compensation)
- periodic reconciliation/recovery:
  - `src/solution0/reaper.py`
- fault-path validation:
  - `tests/fault/test_runtime_faults.py`
  - `tests/fault/test_publish_failure_path.py`
  - `tests/unit/test_reaper_recovery.py`

Evidence:

- `docker compose ps` shows running `worker` and `reaper`
- submit/poll flow reaches `COMPLETED`
- cancel on completed task returns deterministic `409 CONFLICT`
- redis restart resilience:
  - no `NoScriptError` on post-recovery submit paths
  - admission/decrement Lua scripts auto-reload when Redis script cache is lost
- `./scripts/fault_check.sh` passed (`4 passed`) including worker-down and publish-failure compensation paths
