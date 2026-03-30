# P1-018: RabbitMQ readiness check

Priority: P1
Status: done
Depends on: P1-006

## Objective

Add RabbitMQ connectivity to the `/ready` health check endpoint.

## Why

Sol 2 depends on RabbitMQ for all async processing. If RabbitMQ is down, the API can accept tasks but they won't be processed. The readiness probe should reflect this dependency so load balancers and orchestrators can route traffic appropriately.

## Scope

- `src/solution2/api/health_routes.py` (or wherever readiness is defined)
  - Add RabbitMQ connection check to readiness probe
  - Use pika `BlockingConnection` with short timeout, or management API health check
  - Return degraded/not-ready if RabbitMQ unreachable
- Keep existing PG + Redis checks

## Checklist

- [x] `/ready` checks RabbitMQ connectivity
- [x] Short timeout (1-2s) to avoid blocking probe
- [x] Degraded response when RabbitMQ is down but PG/Redis are up
- [x] Tests cover RabbitMQ checker integration in readiness service

## Validation

- `uv run pytest tests/unit/test_health.py -q`
- Manual: stop RabbitMQ container, verify `/ready` returns degraded
