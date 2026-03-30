# Logging Contract and Trace Context (BK-014)

Date: 2026-02-15  
Scope: API, worker, reaper, billing events

## Required Common Fields

All structured logs must include:

- `timestamp`
- `level`
- `logger`
- `event`

Correlation fields (when available):

- `trace_id`
- `task_id`
- `user_id`
- `path`
- `method`

## Event Classes

### API

- Request/response lifecycle
- submit/poll/cancel/admin mutations
- degradation/failure events

### Worker

- model init, task processing, terminal failure, completion
- trace propagation from API to worker task payload (best effort)

### Reaper

- cycle summaries
- orphan/stuck recovery refunds
- snapshot flush counts

### Billing

- deduction/refund decision points
- compensation path outcomes

## Trace Propagation Policy

1. API middleware establishes `trace_id` from `X-Trace-Id` header or generated UUID.
2. Submit path forwards `trace_id` in Celery task args.
3. Worker binds `trace_id` to log context for task execution scope.
4. If trace context is missing, worker uses empty trace field instead of failing request processing.

## Redaction and Sensitive Data

- API keys are never logged.
- Request payload values are not logged by default in hot paths.
- Errors are logged as message strings; secrets should remain outside exception messages.

## Tests and Evidence

- Schema shape:
  - `tests/unit/test_logging_shape.py`
- Trace propagation:
  - `tests/unit/test_app_paths.py::test_submit_accept_path_and_hit_endpoint` (Celery payload includes trace ID)
- Worker context handling:
  - `src/solution0/worker_tasks.py` (`bind_log_context` / `clear_log_context`)
