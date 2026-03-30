# P1-016: Remove Sol 1 Redis key patterns

Priority: P1
Status: done
Depends on: P0-013, P0-014

## Objective

Remove all Sol 1 Redis key helpers and their usage across the codebase. Sol 2 uses a single `task:{id}` hash for query cache, not the Sol 1 two-key pattern.

## Why

Sol 2 has a single Redis key pattern (`task:{id}`) for the query-side cache. The Sol 1 patterns (`result_cache_key`, `task_state_key`, `pending_marker_key`, `credits_cache_key`, `active_tasks_key`) are architectural artifacts that create confusion and potential bugs.

## Scope

- `src/solution2/core/redis_keys.py` (or wherever key helpers live)
  - Delete: `result_cache_key()`, `task_state_key()`, `pending_marker_key()`, `credits_cache_key()`, `active_tasks_key()`
  - Keep: `task_cache_key()` (the Sol 2 `task:{id}` pattern) if it exists, or rename
- All files that import/use deleted helpers (~7 files per grep):
  - `task_write_routes.py` — remove pending_marker usage
  - `task_read_routes.py` — remove result_cache/task_state usage (covered by P0-014)
  - `billing.py` — remove credits_cache/active_tasks usage (covered by P1-015)
  - `admin_routes.py` — remove credits_cache/credits:dirty usage
  - Any worker files referencing old patterns
- `src/solution2/core/redis_keys.py` — delete `redis_tasks_stream_key` if present (Sol 1 stream)

## Checklist

- [x] All Sol 1 key helper functions deleted
- [x] No imports of deleted functions remain
- [x] No string literals matching old patterns (credits:{uid}, active:{uid}, pending:, result:)
- [x] `task:{id}` is the only Redis task-cache key pattern used
- [x] All tests pass

## Validation

- `ruff check src/solution2/`
- `uv run pytest tests/ -q`
- `rg "result_cache_key|pending_marker_key|credits_cache_key|active_tasks_key|idempotency_key\\(" src/solution2/` returns nothing
