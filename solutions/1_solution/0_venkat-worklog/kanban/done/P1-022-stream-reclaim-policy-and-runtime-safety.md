# P1-022: Stream Reclaim Policy and Runtime Safety

Priority: P1
Status: done
Depends on: P1-018

## Objective

Prevent premature stream message reclaim under normal runtime and improve multi-worker safety margins.

## Checklist

- [x] Recalibrate `stream_worker_claim_idle_ms` relative to max modeled runtime + jitter + scheduler overhead
- [x] Add guardrails/tests for multi-worker reclaim behavior so healthy in-flight messages are not reclaimed early
- [x] Review and tune related heartbeat/readiness thresholds for stream worker liveness
- [x] Add stress/fault tests covering long-running tasks with multiple consumers

## Acceptance Criteria

- [x] In-flight healthy tasks are not prematurely reclaimed in normal operation
- [x] PEL recovery still works for genuinely stuck/abandoned messages
- [x] Tests demonstrate stable behavior across concurrent workers

## Notes

- Calibrated defaults and envs:
  - `STREAM_WORKER_CLAIM_IDLE_MS` moved from `2000` to `15000` in `.env.dev.defaults`.
  - `stream_worker_claim_idle_ms` default moved to `15000` in settings.
- Added runtime guardrails in `AppSettings`:
  - reject reclaim windows below modeled runtime safety floor
  - reject heartbeat TTL below block/runtime liveness floor
- Added reclaim/worker safety helpers in `src/solution1/constants.py`.
- Added/updated tests:
  - `tests/unit/test_settings.py`
  - `tests/unit/test_stream_worker.py`
- Verification evidence:
  - `worklog/evidence/full-check-20260216T213751Z/`
  - `worklog/evidence/full-check-20260216T213751Z/scenarios.json`
