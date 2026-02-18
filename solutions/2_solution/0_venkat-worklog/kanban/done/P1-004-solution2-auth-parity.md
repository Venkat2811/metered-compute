# P1-004: Solution2 Auth + JWT Parity with Sol1

Priority: P1
Status: done
Depends on: P1-003

## Objective

Carry over Sol1 OAuth/JWT path with Sol2 service naming and settings without behavior regression.

## Scope

- `src/solution2/services/auth.py`:
  - API key cache-aside lookup
  - PG fallback
  - Redis + PG JTI revocation checks
  - JWKS resolution/cache refresh
- `src/solution2/app.py` middleware:
  - JWT verification (local)
  - scope enforcement
  - revocation check
- `compose.yaml` and bootstrap for Hydra clients unchanged in semantics.
- Remove Sol1 stream admission dependencies.

## Checklist

- [x] `PYJWT` claim extraction and issuer/audience behavior ported from Sol1.
- [x] JTI required and checked as a revocation key.
- [x] Redis-first, PG-fallback revocation lookups validated.
- [x] Hydra auth endpoints remain compatible with existing demo/scripts.

## Acceptance Criteria

- Auth contract tests from Sol1 pass in Sol2 context.
- No auth regressions observed for `/v1/oauth/token`, `/v1/task`, `/v1/task/{id}`, `/v1/auth/revoke`.

## Validation

- `pytest tests/unit/test_auth_service.py tests/unit/test_auth_utils.py -q`
- Manual flow:
  - `POST /v1/oauth/token` with seeded API key
  - authenticated submit and poll
