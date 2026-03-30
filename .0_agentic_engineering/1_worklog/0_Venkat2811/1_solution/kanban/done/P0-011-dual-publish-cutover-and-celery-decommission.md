# P0-011: Dual-Publish Cutover and Celery Decommission

Priority: P0
Status: done
Depends on: P0-006

## Objective

Eliminate dual-publish behavior (`Lua XADD` + `Celery send_task`) and complete the execution-plane cutover to Redis Streams for solution-1 RFC alignment.

## Checklist

- [x] Remove API-side Celery publish path from submit flow
- [x] Ensure stream worker is the only execution consumer
- [x] Remove cancel-time Celery revoke behavior and replace with stream-native cancellation semantics
- [x] Replace Celery readiness probes with stream consumer-group readiness checks
- [x] Rename/update queue metrics from Celery naming to stream semantics (`stream_depth`, `pel_depth`, lag)
- [x] Remove unused Celery settings/contracts/dependencies from runtime path

## Acceptance Criteria

- [x] No task execution occurs through Celery in `1_solution`
- [x] Submit creates exactly one execution intent (stream entry) per accepted task
- [x] Fault/integration tests prove no duplicate execution after cutover
