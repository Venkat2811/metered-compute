# BK-014: Logging Contract and Trace Context Hardening

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Standardize structured logging schema and trace-context propagation so logs are queryable, correlated, and operationally actionable.

## Checklist

- [x] Define required log schema fields by event class (api, worker, reaper, billing)
- [x] Add tests for schema conformance on critical events
- [x] Propagate `trace_id` through async task boundaries where feasible
- [x] Add sampling/redaction policy for noisy or sensitive fields

## Exit Criteria

- [x] Log events are consistently shaped and machine-queryable
- [x] Trace context is preserved across core workflow boundaries
- [x] Logging policy is documented and enforceable

## Evidence

- Logging contract doc: `../../research/2026-02-15-logging-contract.md`
- Schema test: `tests/unit/test_logging_shape.py`
- Trace propagation:
  - API forwards trace to worker payload (`src/solution0/app.py`)
  - worker binds trace context (`src/solution0/worker_tasks.py`)
  - payload assertion test (`tests/unit/test_app_paths.py`)
