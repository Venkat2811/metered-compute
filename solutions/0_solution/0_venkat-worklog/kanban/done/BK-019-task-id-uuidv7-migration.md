# BK-019: Task ID UUIDv7 Migration

Priority: Backlog
Status: done
Depends on: P0-004

## Objective

Align implementation with RFC/matrix requirement that task IDs are UUIDv7 and time-ordered.

## Checklist

- [x] Add UUIDv7 generator dependency compatible with Python 3.12
- [x] Switch submit path task id generation from UUIDv4 to UUIDv7
- [x] Add test assertions for UUIDv7 in unit and integration paths
- [x] Rebuild containers and validate end-to-end behavior

## Exit Criteria

- [x] New task IDs are UUIDv7 in deployed API responses
- [x] Existing task/poll/cancel flows remain fully compatible
- [x] Integration tests pass with UUIDv7 assertions

## Evidence

- Dependency: `pyproject.toml` (`uuid6==2025.0.1`)
- Implementation: `src/solution0/app.py`
- Unit assertion: `tests/unit/test_app_paths.py`
- Integration assertion: `tests/integration/test_api_flow.py`
