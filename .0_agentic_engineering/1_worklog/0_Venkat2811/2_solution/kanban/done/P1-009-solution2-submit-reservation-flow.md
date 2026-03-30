# P1-009: Solution2 Submit + reservation flow

Priority: P1
Status: done
Depends on: P1-008

## Objective

Implement `POST /v1/task` on a single transaction: idempotent key dedupe, credit reservation, command insert, and outbox event emission.

## Scope

- `src/solution2/services/billing.py`
  - Replace stream/Lua path with reservation pipeline:
    - idempotency lookup
    - concurrency check via active reservations
    - `reserve_credits` + `create_reservation` + `create_task_command`
    - `create_outbox_event`
    - commit once all operations succeed
  - Post-commit Redis query cache write (`task:{id}`).
- `src/solution2/api/task_write_routes.py`
  - route validation against mode/tier routing table
  - reject invalid idempotency + malformed requests
- `src/solution2/services/retry.py` / runtime settings
  - remove dependency on stream worker claim/backoff settings where possible.

## Checklist

- [x] Route rejects free-tier sync requests (400 + structured error).
- [x] Concurrency cap enforced per tier and reservation count.
- [x] Insufficient credits returns 402 and no side-effect.
- [x] Duplicate idempotency key with same payload returns same task row.
- [x] Duplicate idempotency with different payload returns 409.
- [x] Post-commit cache write updates status `PENDING` with queue label.

## Notes

- Scope is already implemented by previous DB-path migration; this card closes with regression tests that lock expected behavior at the submit/admission boundary.
- Validation commands run:
  - `uv run pytest -q tests/unit/test_billing_service.py tests/unit/test_app_paths.py`

## Validation

- `pytest tests/unit/test_submit_reservation.py tests/unit/test_app_paths.py -q`
- `pytest tests/integration/test_submit_flow.py -q` (compose required)

## Acceptance Criteria

- No Lua scripts remain on submit path.
- Command insert + outbox row are atomic and visible together.
- Reservation debit/credit invariants are stable under repeated/replayed submits.
