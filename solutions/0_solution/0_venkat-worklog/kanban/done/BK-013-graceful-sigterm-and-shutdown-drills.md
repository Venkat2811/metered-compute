# BK-013: Graceful SIGTERM and Shutdown Drills

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Guarantee predictable shutdown behavior across API, worker, and reaper during rolling deploys and abrupt stop/restart scenarios.

## Checklist

- [x] Add explicit shutdown budgets/timeouts per service
- [x] Add integration drills for stop/restart while requests/tasks are in-flight
- [x] Verify no leaked counters/credits on forced termination scenarios
- [x] Document operational shutdown and restart runbook

## Exit Criteria

- [x] SIGTERM behavior is deterministic and tested
- [x] In-flight work handling is explicit and observable
- [x] Restart does not leave billing/task state inconsistent

## Evidence

- Drill doc: `../../research/2026-02-15-shutdown-and-sigterm-drills.md`
- Runbook updates: `../../RUNBOOK.md` (Section 12)
- Runtime tests:
  - `tests/fault/test_runtime_faults.py`
  - `tests/fault/test_readiness_degradation.py`
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`
  - `tests/unit/test_reaper_paths.py::test_main_async_runs_single_cycle_and_shuts_down`
