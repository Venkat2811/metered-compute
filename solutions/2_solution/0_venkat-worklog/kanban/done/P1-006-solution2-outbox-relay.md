# P1-006: Solution2 Outbox Relay + RabbitMQ Publish Path

Priority: P1
Status: done
Depends on: P1-005

## Objective

Implement a reliable outbox relay that publishes PG outbox rows to RabbitMQ with at-least-once safety and backlog observability.

## Scope

- `src/solution2/services/rabbitmq.py`:
  - exchange/queue topology declaration
  - durable connections with publisher confirms
- `src/solution2/workers/outbox_relay.py`:
  - batch fetch/unpublished loop
  - publish + confirm + mark published
  - sleep/backoff when no work
  - periodic outbox purge
- Queue topology includes:
  - `exchange.tasks` (`topic`)
  - `queue.realtime`, `queue.fast`, `queue.batch`, DLQ queues
  - `webhooks`/`webhooks.dlq`

## Checklist

- [x] Unpublished events are fetched and published in order.
- [x] Confirmed publish marks rows as published exactly once per successful publish.
- [x] Retry/publish crash windows are safe.
- [x] Dead-letter and DLQ bindings created from queue args.
- [x] `outbox_publish_lag_seconds` metric present.

## Acceptance Criteria

- After submit, outbox row appears unpublished then transitions to published after relay cycle.
- Relay restart does not lose or duplicate observable work incorrectly.
- Confirmed via unit tests and code-level relay-path checks.

## Validation

- `pytest tests/unit/test_outbox_relay.py -q`
- `pytest tests/unit/test_rabbitmq_service.py -q`
- `pytest tests/unit/test_repository_cmd_query.py -q`
- `ruff check src/solution2/workers/outbox_relay.py src/solution2/services/rabbitmq.py`
