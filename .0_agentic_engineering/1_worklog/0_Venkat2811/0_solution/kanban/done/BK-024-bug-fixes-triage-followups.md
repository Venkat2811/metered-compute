# BK-024: Bug-Fix Hardening Follow-ups (Solution 0)

Priority: high
Status: done

## Scope

Execute accepted hardening items for `0_solution` from consolidated contributor assessments.

## Accepted Checklist

- [x] `FIX-0-C1`: Remove plaintext `api_key` from pending marker payload (`task_write_routes` + marker readers)
- [x] `FIX-0-C2`: Mask API key in admin warning logs (`admin_routes`)
- [x] `FIX-0-H1`: Add retry/backoff around worker completion post-PG Redis writes
- [x] `FIX-0-H2`: Add retry/backoff around worker failure refund post-PG Redis writes
- [x] `FIX-0-H3`: Add retry/backoff around cancel refund post-PG Redis writes
- [x] `FIX-0-H4`: Add retry/backoff around admin credits cache sync post-PG
- [x] `FIX-0-H5`: Add retry/backoff around reaper stuck-task refund post-PG Redis writes
- [x] `FIX-0-H6`: Add `scan_iter(count=...)` and per-cycle processing cap in reaper
- [x] `FIX-0-H7`: Add settings validation: `task_cost > 0`, `max_concurrent > 0`
- [x] `FIX-0-M1`: Add INT32 bounds for `SubmitTaskRequest.x/y`
- [x] `FIX-0-M2`: Align dataclass boundaries (`models/domain` for shared business types; module-local for worker internals)

## Deferred (not part of this card)

- `FIX-0-H8`: migration-failure startup wrapper in reaper (deferred)

## Done Criteria

- [x] New/updated tests cover changed behavior
- [x] `ruff check` and `mypy --strict` pass
- [x] Relevant unit/integration/fault suites pass
- [x] `make prove` passes from clean state
