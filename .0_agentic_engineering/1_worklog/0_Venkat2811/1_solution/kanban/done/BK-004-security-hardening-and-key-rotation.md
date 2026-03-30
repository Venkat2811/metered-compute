# BK-004: Security Hardening and Key Rotation

Priority: P1
Status: done
Depends on: P0-010

## Objective

Harden non-dev authentication posture with stricter runtime validation and deterministic secret handling while keeping solution1 API behavior unchanged.

## Checklist

- [x] Enforce non-dev secret-hygiene checks in settings validation
- [x] Harden JWT verification behavior around key rotation and claim checks
- [x] Add regression tests for auth edge cases and settings hygiene
- [x] Document production secret/JWKS contracts and rotation workflow

## Acceptance Criteria

- [x] Security-related runtime/behavioral guardrails in app and settings
- [x] Unit tests cover placeholder/revocation/jwks-refresh failure modes
- [x] Documentation reflects `HYDRA_JWKS_CACHE_TTL_SECONDS` and `_FILE` secret inputs
- [x] Card closed in kanban with validation list

## Notes

- `src/solution1/core/settings.py`
  - Added non-dev secret validation for:
    - UUID format on `admin_api_key`, `alice_api_key`, `bob_api_key`
    - placeholder API keys rejection outside `APP_ENV=dev`
    - OAuth client secret placeholder rejection outside `APP_ENV=dev`
    - OAuth secret minimum length enforcement (`>= 24` chars)
    - non-negative `hydra_jwks_cache_ttl_seconds`
- `src/solution1/app.py`
  - Added controlled JWKS client TTL caching with per-process cache dictionary.
  - JWT decode now retries once with forced JWKS refresh on signing-key misses (rotation-safe behavior).
  - `jti` is required and must be non-empty for token processing; revocation checks run deterministically.
- Tests added/updated:
  - `tests/unit/test_app_internals.py`
    - missing `jti` rejection branch
    - JWKS key-miss retry path assertion
  - `tests/unit/test_app_paths.py`
    - compat fixes for `_jwks_client` monkeypatch signatures
  - `tests/unit/test_settings.py`
    - non-dev placeholder/API-key rejection
    - short secret rejection
    - non-UUID API key rejection
    - negative `hydra_jwks_cache_ttl_seconds` rejection
    - `_FILE` secret source support regression
- Docs:
  - `README.md` (security/secret contract and JWKS cache behavior)
  - `worklog/RUNBOOK.md` (non-dev hardening reminders + rotation checks)

## Validation

- `uv run ruff check src/solution1/app.py src/solution1/core/settings.py tests/unit/test_app_internals.py tests/unit/test_settings.py tests/unit/test_app_paths.py`
- `uv run pytest -q tests/unit/test_app_internals.py tests/unit/test_settings.py tests/unit/test_app_paths.py`
