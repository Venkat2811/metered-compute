# P0-003: Hydra OAuth JWT Auth and Revocation Contracts

Priority: P0
Status: done
Depends on: P0-002

## Objective

Integrate Ory Hydra (Go, off-the-shelf OAuth server) and ship OAuth token issuance plus JWT validation contracts for Solution 1 with local verification on API hot path.

Decision captured from architecture review:

- OAuth provider: `Ory Hydra` (Go OSS) for solution-1 baseline.
- No custom in-house OAuth server in this track.

## Checklist

- [x] Add Hydra services to compose (`hydra-migrate`, `hydra`, `hydra-client-init`) with deterministic dev bootstrap
- [x] Provision OAuth clients from deterministic local defaults (admin, user1, user2) with idempotent bootstrap script
- [x] Build `/v1/oauth/token` adapter endpoint that exchanges client credentials against Hydra token endpoint
- [x] Validate incoming API key/client credentials against hashed-key table (through bootstrapped client mapping)
- [x] Issue JWT access tokens with claims carrying `sub`, `tier`, `role`, `jti`, `exp` (Hydra JWT access token strategy)
- [x] Implement API middleware for local signature + claim validation (JWKS/key cache)
- [x] Add revocation checks via Redis keyspace contract
- [x] Add admin authorization guard from role claim
- [x] Add integration test proving Hydra-issued JWT can call protected API paths
- [x] Add integration test proving revoked token path is rejected deterministically

## Acceptance Criteria

- [x] Token issuance + verification paths are fully tested
- [x] Submit/poll paths do not call Postgres for auth validation on cache-hot path
- [x] Invalid/revoked/expired token behaviors return deterministic error contracts
- [x] Compose startup proves Hydra migration + client bootstrap succeeds deterministically
- [x] RFC-0001 auth section is fully satisfied by code and tests

## Progress Notes (2026-02-16, Iteration 1)

Implemented:

- Compose services added: `hydra-migrate`, `hydra`, `hydra-client-init`.
- Idempotent client bootstrap script added at `docker/hydra/bootstrap-clients.sh`.
- API endpoint `POST /v1/oauth/token` added with two request modes:
  - direct `client_id` + `client_secret`
  - dev alias `api_key` -> mapped OAuth client
- Hydra configured to issue JWT access tokens (`OAUTH2_ACCESS_TOKEN_STRATEGY=jwt`).

TDD evidence:

- Red: new oauth endpoint tests failed with `404` before route implementation.
- Green: `tests/unit/test_app_paths.py` oauth tests now pass.
- Validation gates:
  - `make lint type test-unit`
  - compose boot validation with Hydra startup and bootstrap
  - manual token checks against `/v1/oauth/token` for both credential modes

## Progress Notes (2026-02-16, Iteration 2)

Implemented:

- Added local JWT verification path with JWKS client cache and issuer validation.
- Added Redis revocation gate (`revoked:{user_id}` set keyed by token `jti`) before accepting JWT auth context.
- Added role-claim support with deterministic fallback to OAuth client mapping.
- Hardened JWT auth path to return `503` on unexpected auth dependency failures.

TDD evidence:

- Red: added tests for revoked-token rejection and admin-role-claim authorization.
- Green: implemented revocation key contract + role extraction and reran unit suite.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`

## Progress Notes (2026-02-16, Iteration 3)

Implemented:

- Added integration suite `tests/integration/test_oauth_jwt_flow.py` for real Hydra token exchange.
- Added protected-path test proving Hydra-issued JWT can submit and poll tasks end-to-end.
- Added revoked-token integration test by writing `jti` into Redis `revoked:{user_id}`.
- Adjusted JWT user resolution to map known OAuth client IDs back to canonical API keys so credits/admission use the same user identity in Redis and Postgres.

TDD evidence:

- Red: new integration test failed with `401` on submit due identity mismatch.
- Green: implemented client-id -> api-key resolution in JWT auth path; integration test passed.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `pytest tests/integration/test_oauth_jwt_flow.py -m integration -q`

## Progress Notes (2026-02-16, Iteration 4)

Implemented:

- Added hashed-key-table validation for OAuth `api_key` alias exchange (`api_keys.key_hash` + `is_active=true`).
- Added unit coverage for OAuth key-validation success/failure/degraded paths (`200`, `401`, `503`).
- Added JWT cache-hot-path integration test to ensure repeated authenticated polls avoid repeated Postgres lookups.
- Added expired-token unit coverage to make invalid/revoked/expired behavior deterministic.

TDD evidence:

- Red: added new unit/integration expectations before auth-path updates.
- Green: implemented repository hash validation + route guard and reran suite.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `make test-integration`
