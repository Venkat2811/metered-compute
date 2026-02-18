# BK-020: Worker Runtime and Loop Model Hardening

Priority: Backlog
Status: done
Depends on: BK-012

## Objective

Replace fragile per-process `run_until_complete` orchestration with a safer execution model for Celery workers.

## Checklist

- [x] Evaluate synchronous DB driver path for Celery worker vs isolated async worker process
- [x] Add bounded timeouts around DB operations in worker terminal paths
- [x] Document and test worker behavior under DB hang and reconnect scenarios

## Exit Criteria

- [x] Worker execution model is resilient under dependency slowness and process lifecycle events

## Evidence

- Runtime model migrated from `run_until_complete` to dedicated worker-loop thread + `run_coroutine_threadsafe`: `src/solution0/worker_tasks.py`
- Bounded DB-operation timeout controls added: `src/solution0/settings.py`, `src/solution0/worker_tasks.py`
- Loop bootstrap/shutdown timeout controls added: `src/solution0/settings.py`, `src/solution0/worker_tasks.py`
- Worker runtime behavior tests:
  - `tests/unit/test_worker_tasks_runtime.py::test_run_task_success_path`
  - `tests/unit/test_worker_tasks_runtime.py::test_run_task_terminal_failure_refunds_and_marks_failed`
  - `tests/unit/test_worker_tasks_runtime.py::test_bootstrap_runtime_runs_migrations_and_loads_scripts`
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`
