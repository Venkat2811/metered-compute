# BK-009: JWT Boundary Hardening and Principal Resolution

Priority: P1
Status: done
Depends on: P1-021

## Objective

Capture defense-in-depth JWT hardening (audience strictness, broader principal mapping model) for post-ship iteration.

## Checklist

- [x] Evaluate `aud` enforcement impact with Hydra issuance contract
- [x] Design scalable claim-to-principal resolution strategy
- [x] Expand auth hardening tests for edge cases

## Acceptance Criteria

- [x] Hardening plan is implementation-ready without destabilizing current flow

## Notes

- Added config-driven audience enforcement in JWT decode path:
  - `hydra_expected_audience` in settings (optional).
  - when configured, JWT decode enables `verify_aud=True` and passes expected audience.
- Tightened principal resolution boundaries:
  - reject tokens where `client_id` and `sub` disagree.
  - reject invalid or mismatched `role` claims.
  - reject invalid or mismatched `tier` claims.
  - still derives base principal from deterministic client mapping to keep local/dev reproducibility.
- Expanded auth boundary tests in `tests/unit/test_app_internals.py`:
  - invalid role/tier rejection
  - mismatched `client_id/sub` rejection
  - audience enforcement branch (decode called with expected audience + verify_aud)
  - valid role/tier path remains accepted

## Validation

- `ruff check src/solution1/app.py src/solution1/core/settings.py tests/unit/test_app_internals.py`
- `pytest -q tests/unit/test_app_internals.py`
- `pytest -q tests/unit/test_app_paths.py`
