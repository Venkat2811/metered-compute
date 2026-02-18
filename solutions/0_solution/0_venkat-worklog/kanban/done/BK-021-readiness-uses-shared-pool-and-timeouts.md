# BK-021: Readiness Uses Shared Pool and Timeouts

Priority: Backlog
Status: done
Depends on: P0-006

## Objective

Eliminate readiness probe connection churn by using shared runtime resources (DB pool/Redis client) with strict timeouts.

## Checklist

- [x] Refactor dependency checks to avoid creating fresh PG connections per probe
- [x] Add timeout budgets and failure-mode tests for readiness endpoints
- [x] Validate behavior under high probe frequency

## Exit Criteria

- [x] Readiness checks are low-overhead and do not amplify DB connection pressure

## Evidence

- Shared-resource readiness checks implemented: `src/solution0/dependencies.py`
- Lifespan wiring now passes shared `db_pool` and `redis_client` to dependency health service: `src/solution0/app.py`
- Timeout settings surfaced and configurable: `src/solution0/settings.py`, `.env.dev.defaults`
- Unit coverage:
  - `tests/unit/test_dependency_health.py::test_check_postgres_pool_uses_shared_pool`
  - `tests/unit/test_dependency_health.py::test_check_redis_client_uses_shared_client`
  - `tests/unit/test_dependency_health.py::test_build_dependency_health_service_prefers_shared_resources`
