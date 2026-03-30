# P0-012: JWT Hot Path, Revocation Retention, and Token Error Contracts

Priority: P0
Status: done
Depends on: P0-003

## Objective

Align auth behavior with RFC-0001 intent: local JWT verification on hot path, explicit token lifecycle errors, and bounded revocation storage.

## Checklist

- [x] Remove JWT-path dependency on API-key resolver (`resolve_user_from_api_key`) for mapped clients
- [x] Construct authenticated principal from verified JWT claims (`sub/client_id`, `role`, `tier`) with strict validation
- [x] Keep revocation check in Redis only, with bounded retention strategy (TTL/day-sharded keyspace)
- [x] Add explicit expired-token handling and API error code/message contract
- [x] Add tests for: no DB lookup on JWT hot path, revocation check behavior, expired vs invalid token responses
- [x] Document dev/prod revocation retention policy in README/RFC notes

## Acceptance Criteria

- [x] JWT-authenticated requests do not call Postgres/Redis auth-cache lookup paths
- [x] Revocation keyspace remains bounded without manual full scans
- [x] Clients can distinguish `TOKEN_EXPIRED` from generic invalid token failures
