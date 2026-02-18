# P0-004: Task API Contracts and Error Taxonomy

Priority: P0
Status: done
Depends on: P0-003

## Objective

Ship typed API contracts for submit/poll/cancel/admin endpoints, aligned to shared error taxonomy and RFC behavior.

## Checklist

- [x] Implement endpoints:
  - [x] `POST /v1/task`
  - [x] `GET /v1/poll`
  - [x] `POST /v1/task/{id}/cancel`
  - [x] `POST /v1/admin/credits`
  - [x] `GET /health`, `GET /ready`
- [x] Add typed request/response models and shared error envelope
- [x] Support `Idempotency-Key` header semantics
- [x] Enforce authorization constraints (admin-only top-up)

## TDD Subtasks

1. Red

- [x] Add contract tests for success and every expected error code (400/401/402/404/409/429/503)
- [x] Add failing idempotency conflict test (same key + changed payload)

2. Green

- [x] Implement endpoint handlers and validation until contract tests pass

3. Refactor

- [x] Centralize exception mapping and typed response builders

## Acceptance Criteria

- [x] API behavior matches RFC and shared assumptions
- [x] Response models are fully typed and validated
- [x] Error semantics are deterministic and test-backed

## Progress Notes (2026-02-15)

Implemented:

- endpoint contracts and handlers:
  - `src/solution0/app.py`
  - `src/solution0/schemas.py`
- repository and domain mapping:
  - `src/solution0/domain.py`
  - `src/solution0/db/repository.py`
- idempotency replay + error envelope with stable codes for `401/402/404/409/429/503`
- explicit contract coverage:
  - `tests/integration/test_error_contracts.py`

Evidence:

- `./scripts/integration_check.sh` passed (`7 integration`, `1 e2e`)
- compose smoke flow:
  - `POST /v1/task` => `201`
  - replay same idempotency key => `200` same `task_id`
  - `GET /v1/poll` terminal completion => `200`
  - `POST /v1/task/{id}/cancel` on terminal task => `409`
  - `POST /v1/admin/credits` with admin token => `200`
