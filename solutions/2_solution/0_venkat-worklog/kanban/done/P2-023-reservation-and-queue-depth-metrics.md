# P2-023: Reservation state and queue depth metrics

Priority: P2
Status: done

## Objective

Add Prometheus metrics for reservation lifecycle and RabbitMQ queue depths.

## Scope

- `reservations_active_gauge` — count of RESERVED state reservations
- `reservations_captured_total`, `reservations_released_total` — counters
- RabbitMQ queue depth per SLA queue (via management API or custom consumer metric)
- Grafana dashboard panels for reservation and queue metrics

## Notes

- Sol 0 and Sol 1 have queue depth metrics; Sol 2 should too
- RabbitMQ management plugin exposes queue lengths via HTTP API

## Implementation summary

- Added metrics:
  - `reservations_active_gauge`
  - `reservations_captured_total`
  - `reservations_released_total`
  - `rabbitmq_queue_depth{queue}`
- API admission path increments `reservations_active_gauge` on successful reservation create.
- Worker success/failure transitions update reservation capture/release counters and active gauge decrements.
- Cancel path updates reservation release counter + active gauge decrement after successful release/refund.
- Watchdog refreshes `reservations_active_gauge` each cycle from authoritative Postgres count and increments release counter for timed-out releases.
- Worker publishes per-SLA queue depth (`queue.realtime`, `queue.fast`, `queue.batch`) via passive queue inspection.
- Updated Grafana dashboard (`solution2-overview.json`) with RabbitMQ queue-depth and reservation panels.

## Validation

- `pytest tests/unit/test_billing_service.py tests/unit/test_repository_cmd_query.py tests/unit/test_worker.py tests/unit/test_watchdog.py tests/unit/test_observability_contract.py -q`
- `pytest tests/integration/test_multi_user_concurrency.py::test_multi_user_concurrency_enforced_per_user tests/integration/test_oauth_jwt_flow.py::test_jwt_tier_based_concurrency_envelopes -q`
