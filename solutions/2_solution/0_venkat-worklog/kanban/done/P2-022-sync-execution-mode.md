# P2-022: Sync execution mode

Priority: P2
Status: done

## Objective

Implement synchronous execution mode for enterprise-tier realtime requests where the API holds the connection and returns results inline.

## Scope

- Enterprise tier + sync mode + small model path executes inline and returns terminal result
- Uses transactional sync reservation lifecycle (admit -> running -> completed/failed/timeout)
- Bypasses RabbitMQ/outbox for inline execution path only
- Timeout guard returns `408 REQUEST_TIMEOUT` with structured error

## Notes

- RFC-0002 mentions sync as enterprise differentiator
- Implementation now lives in:
  - `src/solution2/api/task_write_routes.py`
  - `src/solution2/services/billing.py`
  - `src/solution2/core/settings.py`
  - `src/solution2/models/schemas.py`

## Validation

- `make gate-unit`
- `make prove`
