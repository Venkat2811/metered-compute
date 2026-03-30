# P1-015: JWT-Only Protected Auth and Route Scope Enforcement

Priority: P1
Status: done
Depends on: P0-003, P0-012

## Objective

Make solution 1 a clean JWT break for protected APIs and enforce per-route OAuth scopes without DB lookups on request hot paths.

## Checklist

- [x] Remove API-key bearer fallback from protected endpoints (`/v1/task`, `/v1/poll`, `/v1/task/{id}/cancel`, `/v1/admin/credits`, compat twins)
- [x] Add scope parsing from JWT claims and carry scopes in authenticated principal
- [x] Enforce required scopes:
  - [x] `task:submit` on submit
  - [x] `task:poll` on poll
  - [x] `task:cancel` on cancel
  - [x] `admin:credits` on admin credit endpoint
- [x] Keep admin role authorization in addition to scope requirement for admin endpoint
- [x] Ensure JWT verification remains local crypto + Redis revocation check only
- [x] Update tests (unit/integration/fault/e2e/scenario helpers) to obtain and use OAuth tokens
- [x] Update demo scripts to exchange token first, then call protected routes with JWT

## Acceptance Criteria

- [x] Protected routes reject non-JWT bearer tokens with `401 UNAUTHORIZED`
- [x] Missing scope yields `403 FORBIDDEN` without DB lookup
- [x] Existing JWT hot-path no-DB auth invariant test still passes
- [x] `make gate-unit`, `make gate-integration`, and `make gate-fault` pass for changed auth/scope contracts
