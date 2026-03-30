# P1-015: Remove Sol 1 billing dead code

Priority: P1
Status: done
Depends on: P0-013

## Objective

Remove the Redis-native admission gate fallback and Sol 1 compensation functions from billing.py that are dead code in Sol 2.

## Why

Sol 2 always has `db_pool` available, so `_run_redis_admission_gate()` is unreachable. The Sol 1 compensation functions (`mark_credit_dirty`, `refund_and_decrement_active`, `decrement_active_counter`) operate on Sol 1 Redis keys that Sol 2 doesn't use. Keeping them creates confusion about the actual billing model.

## Scope

- `src/solution2/services/billing.py`
  - Delete `_run_redis_admission_gate()` (lines ~258-350) — dead code, PG path always runs
  - Delete `mark_credit_dirty()` — Sol 1 `credits:dirty` set pattern
  - Delete `refund_and_decrement_active()` — Sol 1 `credits:{uid}` + `active:{uid}` pattern
  - Delete `decrement_active_counter()` — Sol 1 `active:{uid}` pattern
  - Remove the `if db_pool is None` branch in `run_admission_gate()` (it can't happen)
  - Delete `CACHE_MISS` from `AdmissionResult` enum if only used by Redis path
  - Clean imports

## Checklist

- [x] `_run_redis_admission_gate()` deleted
- [x] `mark_credit_dirty()` deleted
- [x] `refund_and_decrement_active()` deleted
- [x] `decrement_active_counter()` deleted
- [x] `CACHE_MISS` handling retained only at route boundary (no billing emitter)
- [x] No callers reference deleted functions
- [x] All tests pass after removal

## Validation

- `uv run pytest tests/ -q`
- `ruff check src/solution2/services/billing.py`
- `mypy --strict src/solution2/services/billing.py`
