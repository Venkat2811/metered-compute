# BK-003: OTel Tempo Runtime Profile

Priority: P2
Status: done
Depends on: P0-010

## Objective

Promote OTel+Tempo from config-only artifacts to optional runtime compose profile for local tracing demo.

## Checklist

- [x] Add optional compose profile for OTel collector + Tempo
- [x] Wire trace export from API, worker, and reconciler
- [x] Provide dashboard/query examples in runbook

## Acceptance Criteria

- [x] Optional profile runs without affecting baseline demo path
- [x] Trace spans show API -> stream -> worker lifecycle

## What changed

- Added runtime tracing module:
  - `src/solution1/observability/tracing.py`
  - process bootstrap (`configure_process_tracing`)
  - context propagation helpers (`inject_current_trace_context`, `extract_trace_context`)
  - shared span wrapper (`start_span`)
- Wired tracing across services:
  - API: HTTP server spans in `src/solution1/app.py` and trace-context injection in `src/solution1/api/task_write_routes.py`
  - Worker: consumer spans with propagated parent context in `src/solution1/workers/stream_worker.py`
  - Reaper: cycle spans in `src/solution1/workers/reaper.py`
- Enabled compose/runtime toggles:
  - Added OTel env settings to `.env.dev.defaults`
  - Added compose overrides for `OTEL_ENABLED` + exporter vars in `compose.yaml`
- Added tests:
  - `tests/unit/test_tracing_runtime.py`
  - Updated `tests/unit/test_app_paths.py`, `tests/unit/test_stream_worker.py`, `tests/unit/test_observability_configs.py`, `tests/unit/test_settings.py`
- Added runbook section:
  - `README.md` tracing profile startup and TraceQL examples

## Validation run

- `ruff check src/solution1/observability/tracing.py src/solution1/app.py src/solution1/api/task_write_routes.py src/solution1/workers/stream_worker.py src/solution1/workers/reaper.py tests/unit/test_tracing_runtime.py tests/unit/test_app_paths.py tests/unit/test_stream_worker.py tests/unit/test_settings.py tests/unit/test_observability_configs.py`
- `mypy --strict src tests`
- `pytest -q tests/unit/test_tracing_runtime.py tests/unit/test_app_paths.py tests/unit/test_stream_worker.py tests/unit/test_reaper_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_reaper_retention.py tests/unit/test_observability_configs.py tests/unit/test_settings.py`
