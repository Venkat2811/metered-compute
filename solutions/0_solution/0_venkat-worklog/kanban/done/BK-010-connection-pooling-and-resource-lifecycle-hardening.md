# BK-010: Connection Pooling and Resource Lifecycle Hardening

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Harden database/redis connection management across API, worker, and reaper with explicit sizing strategy and clean lifecycle handling.

## Checklist

- [x] Define per-service pool sizing policy and defaults for local vs production
- [x] Add pool exhaustion behavior tests and timeout assertions
- [x] Ensure all services close pools/clients on shutdown paths
- [x] Add runbook notes for pool tuning and saturation signals

## Exit Criteria

- [x] No per-request or per-task pool construction in hot paths
- [x] Lifecycle open/close behavior is deterministic and test-backed
- [x] Pool sizing assumptions are explicit and reviewable

## Evidence

- Policy doc: `../../research/2026-02-15-pool-lifecycle-policy.md`
- Runbook updates: `../../RUNBOOK.md`
- Pool exhaustion path test: `tests/unit/test_app_paths.py::test_submit_returns_503_on_pool_exhaustion`
- Lifecycle close tests:
  - `tests/unit/test_app_internals.py::test_lifespan_initializes_runtime_and_closes_resources`
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`
  - `tests/unit/test_reaper_paths.py::test_main_async_runs_single_cycle_and_shuts_down`
