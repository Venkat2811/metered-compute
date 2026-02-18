# P0-006: Observability - Prometheus, Grafana, Structured Logs

Priority: P0
Status: done
Depends on: P0-005

## Objective

Add production-grade observability for Solution 0: structured logs, metrics, dashboard provisioning, and actionable alerts.

## Checklist

- [x] Add `structlog` JSON logs in API + worker + reaper
- [x] Ensure correlation keys in all critical events (`task_id`, `user_id`, `trace_id`)
- [x] Export Prometheus metrics from API and worker
- [x] Compose wiring for Prometheus + Grafana
- [x] Provision baseline Grafana dashboard JSON
- [x] Add Alertmanager rules file (documented if not run in compose)

## TDD Subtasks

1. Red

- [x] Add failing tests for log event shape and required keys
- [x] Add failing metrics exposure tests (`/metrics` contains expected series)

2. Green

- [x] Implement logging/metrics and pass tests

3. Refactor

- [x] Remove high-cardinality labels; normalize metric dimensions

## Acceptance Criteria

- [x] Observability stack starts in Docker Compose
- [x] Core operational metrics and error counters are visible
- [x] Dashboard and alert rules map to RFC critical paths

## Progress Notes (2026-02-15)

Implemented:

- logging + correlation:
  - `src/solution0/logging_utils.py`
  - structured event emitters in `src/solution0/app.py`, `src/solution0/worker_tasks.py`, `src/solution0/reaper.py`
- metrics:
  - `src/solution0/metrics.py`
  - `/metrics` endpoint in API and worker metrics exporter on `:9100`
- compose observability stack and provisioning:
  - `compose.yaml`
  - `prometheus/prometheus.yml`
  - `prometheus/alerts.yml`
  - `grafana/provisioning/datasources/datasource.yml`
  - `grafana/provisioning/dashboards/dashboard.yml`
  - `grafana/dashboards/solution0-overview.json`
- observability contract tests:
  - `tests/unit/test_logging_shape.py`
  - `tests/integration/test_error_contracts.py` (metrics series assertions)

Evidence:

- `docker compose ps` shows running `prometheus` and `grafana`
- `curl http://localhost:8000/metrics` returns Prometheus series
- `./scripts/ci_check.sh` and `./scripts/integration_check.sh` pass with structured-log and metrics assertions
