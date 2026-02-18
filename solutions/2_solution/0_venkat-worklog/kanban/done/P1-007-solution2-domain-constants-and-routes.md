# P1-007: Solution2 Domain constants and routing contracts

Priority: P1
Status: done
Depends on: P1-001

## Objective

Define solution-2 domain contracts before changing implementation logic, replacing solution-1 stream-oriented constants with Sol2 CQRS/RabbitMQ semantics.

## Scope

- Update `src/solution2/constants.py`:
  - `Tier`, `RequestMode`, `ReservationState` enums.
  - keep existing task constants and add request-mode + queue/routing helpers.
  - Add/adjust helpers:
    - `task_cost_for_model(base_cost, model_class)`
    - `max_concurrent_for_tier(base_max, tier)`
    - `resolve_queue(tier, mode, model_class)` → queue name
    - `compute_routing_key(mode, tier, model_class)` → `tasks.<mode>.<tier>.<model_class>`
- Remove/replace stream-specific names in routing decisions:
  - `minimum_stream_claim_idle_ms` usage should be isolated to compatibility paths only.
- Align with RFC routing matrix:
  - free: async/batch -> `queue.batch`, sync rejected.
  - pro: sync small -> `queue.fast`, sync medium/large rejected.
  - enterprise: sync/async -> `queue.realtime`, batch -> `queue.fast`.

## Checklist

- [x] Add `RequestMode` enum (`async`, `sync`, `batch`).
- [x] Add `Tier` and `ReservationState` enums.
- [x] Add routing helpers and constants with tests.
- [x] Keep stream constants isolated and out of routing paths.
- [x] New routing/unit tests added.

## Validation

- `ruff check src/solution2/constants.py tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_reservation_state.py`
- `pytest -q tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_reservation_state.py`

## Acceptance Criteria

- Invalid mode/tier/model combinations are rejected early and consistently.
- `compute_routing_key` output always follows `tasks.<mode>.<tier>.<model_class>`.
- No direct stream constants influence queue decision logic.
