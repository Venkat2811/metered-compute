# P0-008: Observability Metrics Dashboard and Events

Priority: P0
Status: done
Depends on: P0-005, P0-006, P0-007

## Objective

Provide production-grade observability baseline for Solution 1 and emit searchable business events in structured logs.

## Checklist

- [x] Structured JSON logging with correlation ids (`trace_id`, `task_id`, `user_id`)
- [x] Prometheus metrics for API, Lua, stream lag/PEL, JWT validation, reconciler loops
- [x] Grafana dashboards for throughput, error rates, queue health, credit drift
- [x] Alert rules config for lag, drift, service availability
- [x] OTel/Tempo config artifacts aligned with matrix (RFC/config scope)

## Acceptance Criteria

- [x] `/metrics` coverage includes all core runtime components
- [x] Dashboard and alert config are versioned and reproducible
- [x] Lifecycle and billing events are emitted as structured JSON lines
