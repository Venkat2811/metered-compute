# P0-005: Task API Contracts Submit Poll Cancel Admin

Priority: P0
Status: done
Depends on: P0-003, P0-004

## Objective

Ship public API contracts for submit/poll/cancel/admin with strict schemas, authorization, and stable error taxonomy.

## Checklist

- [x] Implement endpoints: `POST /v1/task`, `GET /v1/poll`, `POST /v1/task/{id}/cancel`, `POST /v1/admin/credits`
- [x] Enforce ownership checks and idempotency-key semantics
- [x] Implement queue position and estimated runtime response fields for pending tasks
- [x] Add compatibility aliases if required by assignment wording
- [x] Define structured business events for lifecycle + billing actions

## Acceptance Criteria

- [x] Endpoint contracts match RFC-0001 and matrix
- [x] Error responses are stable for `400/401/402/404/409/429/503`
- [x] Poll happy path remains Redis-only on data lookup

## Progress Notes (2026-02-16, Iteration 1)

Implemented:

- Kept API contract surface stable across submit/poll/cancel/admin plus assignment compatibility aliases.
- Added Redis task-state key helper (`task:{task_id}`) and poll-path lookup before Postgres fallback.
- Poll now serves pending/running status directly from Redis task hash on happy path.
- Queue position and ETA now use Redis stream depth (`XLEN`) instead of Celery list depth.

TDD evidence:

- Red: added `test_poll_uses_redis_task_state_without_db_lookup` asserting DB is not touched.
- Green: implemented Redis-first poll path and stream-based queue depth.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `make test-integration`

## Progress Notes (2026-02-16, Iteration 2)

Implemented:

- Added structured business events on task write paths:
  - `business_event_task_submitted`
  - `business_event_task_idempotent_replay`
  - `business_event_task_rejected`
  - `business_event_task_cancelled`
- Added structured admin billing event:
  - `business_event_admin_credit_adjusted`

Validation evidence:

- API contract and error-taxonomy stability verified via integration suites:
  - `tests/integration/test_api_flow.py`
  - `tests/integration/test_error_contracts.py`
  - `tests/integration/test_oauth_jwt_flow.py`
