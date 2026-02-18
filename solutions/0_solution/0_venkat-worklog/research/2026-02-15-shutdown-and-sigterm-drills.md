# Graceful Shutdown and SIGTERM Drills (BK-013)

Date: 2026-02-15  
Scope: API, worker, reaper under Docker Compose

## Shutdown Budgets

- API (Uvicorn): `--timeout-graceful-shutdown 20`
- Worker: Celery process shutdown hook closes DB loop and Redis client
- Reaper: SIGTERM/SIGINT handlers stop loop and close DB/Redis in `finally`

## Drill Scenarios

1. Worker stop during active system use
   - command: `docker compose stop worker`
   - expected:
     - submit can still enqueue or be cancelled
     - cancel path refunds and converges task state
2. Redis restart / transient outage
   - command: `docker compose stop redis && docker compose start redis`
   - expected:
     - readiness degrades then recovers
     - submit path does not emit 500 on script cache loss
3. Postgres stop / restart
   - command: `docker compose stop postgres && docker compose start postgres`
   - expected:
     - readiness degrades to 503
     - submit path returns controlled degradation response

## In-Flight Integrity Expectations

- No leaked credits on terminal failures:
  - publish failure -> compensation + audit row
  - worker failure -> refund + audit row
  - stuck recovery -> reaper refund + audit row
- Active counters are decremented using clamp Lua script to avoid negative drift.

## Evidence

- Fault tests:
  - `tests/fault/test_runtime_faults.py::test_worker_crash_allows_cancel_path`
  - `tests/fault/test_runtime_faults.py::test_postgres_down_readiness_and_submit_degrade`
  - `tests/fault/test_readiness_degradation.py::test_ready_degrades_when_redis_is_down_and_recovers`
- Unit lifecycle tests:
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`
  - `tests/unit/test_reaper_paths.py::test_main_async_runs_single_cycle_and_shuts_down`
  - `tests/unit/test_app_internals.py::test_lifespan_initializes_runtime_and_closes_resources`
