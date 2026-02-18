# P1-003: Solution2 Domain Constants and Contract Types

Priority: P1
Status: done
Depends on: P1-002

## Objective

Define and test Sol2 domain constants, enums, and request/task contracts before route/business logic implementation.

## Scope

- Add/update `src/solution2/constants.py`:
  - `TaskStatus`, `ReservationState`, `UserRole`, `Tier`, `ModelClass`, `RequestMode`
  - cost/routing helpers (`task_cost_for_model`, `max_concurrent_for_tier`, `resolve_queue`, `compute_routing_key`)
  - `MODEL_*`, `TIER_*` policy constants
- Add dataclasses:
  - `AuthUser`, `TaskCommand`, `CreditReservation`, `OutboxEvent`, `TaskQueryView`, `WebhookTerminalEvent`
- Pydantic schemas for submit/poll/cancel/batch/admin/webhook/error contract.
- Add tests for reservation/state/cost/routing behavior.

## Checklist

- [x] Enums added with explicit accepted literals.
- [x] Cost multipliers and runtime constants match RFC table.
- [x] Queue resolver covers async/sync/batch + tier/model-class behavior.
- [x] Routing key format matches `tasks.<mode>.<tier>.<model_class>`.
- [x] Error envelope/schema compatibility with existing Sol1 contract preserved.

## Acceptance Criteria

- `ruff` and unit tests for invariants pass.
- Invalid routing/model/tier combinations rejected predictably with 400-equivalent errors.

## Validation

- `pytest tests/unit/test_reservation_state.py tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_task_state.py -q`
