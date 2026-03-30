# BK-022: Auth Cache Contract Cleanup

Priority: Backlog
Status: done
Depends on: P0-003

## Objective

Remove misleading mutable billing fields from auth cache payloads and keep auth cache strictly identity/authorization scoped.

## Checklist

- [x] Remove `credits` from `auth:{api_key}` hash payload
- [x] Ensure no runtime code path depends on auth-cache credits
- [x] Add tests proving auth correctness remains unchanged

## Exit Criteria

- [x] Auth cache schema is minimal, explicit, and non-overlapping with billing state

## Evidence

- Auth cache payload trimmed to identity-only fields (`user_id`, `name`, `role`): `src/solution0/services/auth.py`
- Legacy/invalid auth-cache schema falls back to DB lookup: `src/solution0/services/auth.py`
- Unit coverage:
  - `tests/unit/test_auth_service.py::test_resolve_user_cache_hit_skips_db`
  - `tests/unit/test_auth_service.py::test_resolve_user_tolerates_cache_population_failure`
  - `tests/unit/test_auth_service.py::test_resolve_user_falls_back_to_db_when_auth_cache_schema_is_invalid`
