# BK-013: Bug-Fix Hardening Follow-ups (Solution 1)

Priority: high
Status: done

## Scope

Execute accepted hardening items for `1_solution` from consolidated reviewer assessments.

## Accepted Checklist

- [x] `FIX-1-C1`: Remove plaintext `api_key` from pending marker payload (`task_write_routes` + marker readers)
- [x] `FIX-1-H1`: Add retry/backoff around worker completion post-PG Redis writes
- [x] `FIX-1-H2`: Add retry/backoff around worker failure refund post-PG Redis writes
- [x] `FIX-1-H3`: Add retry/backoff around cancel refund/state post-PG Redis writes
- [x] `FIX-1-H4`: Add retry/backoff around admin credits cache sync post-PG
- [x] `FIX-1-H5`: Add retry/backoff around reaper stuck-task refund post-PG Redis writes
- [x] `FIX-1-H6`: Add `scan_iter(count=...)` and per-cycle processing cap in reaper
- [x] `FIX-1-H7`: Fail startup on revocation rehydration failure (fail-closed)
- [x] `FIX-1-H8`: Bound webhook pending queue length (`MAXLEN`/trim + config)
- [x] `FIX-1-H9`: Add settings validation: `task_cost > 0`, `max_concurrent > 0`
- [x] `FIX-1-M2`: Align dataclass boundaries (`models/domain` for shared business types; module-local for worker internals)

## Deferred / Rejected / Already Fixed

- Deferred: `FIX-1-C2`, `FIX-1-H10`
- Rejected as written: `FIX-1-C3`
- Already fixed: `FIX-1-M1`

## Done Criteria

- [x] New/updated tests cover changed behavior
- [x] `ruff check` and `mypy --strict` pass
- [x] Relevant unit/integration/fault suites pass
- [x] `make prove` passes from clean state
