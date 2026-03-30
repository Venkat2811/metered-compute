# P1-021: Spec Alignment for Submit Contract, Model Cost, and Worker Warmup

Priority: P1
Status: done
Depends on: P1-018

## Objective

Align implemented behavior with agreed solution-1 contract where mismatches were identified.

## Checklist

- [x] Add `estimated_seconds` to submit response model and route response payload
- [x] Resolve and align LARGE model cost factor with shared assumptions/RFC, then enforce in code + tests
- [x] Add explicit 10-second worker model initialization warmup behavior required by spec baseline
- [x] Add poll terminal edge fallback for partial Redis cache presence (`task:{id}` present, `result:{id}` missing)
- [x] Add idempotency key boundary guard (`<= 128`) with deterministic error behavior
- [x] Add/adjust integration tests for submit contract fields and model/tier behavior

## Acceptance Criteria

- [x] Submit response contract matches documented API behavior
- [x] Model/tier math is consistent across code, tests, and docs
- [x] Worker startup behavior matches simulation expectations

## Notes

- Submit contract now includes `estimated_seconds` for both fresh submits and idempotent replays.
- Model cost alignment updated to `LARGE=5` and reflected in scenario + integration expectations.
- Worker model startup now has explicit one-time warmup behavior (10s) with unit coverage.
- Poll path now handles terminal `task:{id}` state when `result:{id}` is absent by falling back to PG.
- Idempotency key validation now enforces non-empty trimmed value and max length `128`.
- Tests updated:
  - `tests/unit/test_app_paths.py`
  - `tests/unit/test_stream_worker.py`
  - `tests/integration/test_api_flow.py`
  - `tests/integration/test_oauth_jwt_flow.py`
- Verification evidence:
  - `worklog/evidence/full-check-20260216T215833Z/`
  - `worklog/evidence/full-check-20260216T220559Z/`
