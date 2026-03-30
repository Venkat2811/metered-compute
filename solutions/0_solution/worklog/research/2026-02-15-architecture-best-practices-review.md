# Architecture and Best-Practices Review (BK-007)

Date: 2026-02-15  
Scope: `0_solution` RFC baseline

## Executive Summary

Solution 0 is spec-complete and production-oriented for its scope. Core strengths are strong admission control, explicit compensation paths, deterministic health/readiness contracts, and high test coverage on reliability-critical modules.

Priority order validation:

1. Correctness: strong
2. Reliability/HA (within compose constraints): strong
3. Scalability/maintainability: moderate-good
4. Performance/complexity: pragmatic baseline with known ceilings

## Review by Domain

### Async runtime and web serving

- FastAPI + Uvicorn usage is correct for this scope.
- API lifespan initializes shared resources once and closes cleanly.
- Blocking work is isolated to worker process; API path remains async.
- Readiness now validates dependency probes, worker connectivity, and Redis script availability.

### Postgres

- Migrations are ordered, validated, and tracked via `schema_migrations`.
- Indexes align to dominant access patterns (`tasks(status,created_at)`, `tasks(user_id,created_at)`, credit txn history).
- Multi-step write paths use explicit transaction boundaries.
- Lock profile under stress indicates low contention in baseline load envelope.

### Redis

- Key design and TTL strategy are coherent for auth cache, idempotency, and runtime counters.
- Lua admission gate keeps hot-path checks atomic.
- NoScript recovery logic handles Redis script-cache loss safely.
- Dirty-credit snapshot flushing exists for persistence reconciliation.

### Celery

- Queue and worker wiring are correct for spec baseline.
- Retry semantics and terminal refund path are explicit.
- Cancellation path combines revoke + refund + status update.

### Lua scripting

- Admission and active-counter scripts have typed parser coverage and fallback reload handling.
- Script availability is now part of readiness contract.

### Reaper

- Handles orphan markers, stuck RUNNING tasks, snapshot flush, and result expiry.
- Recovery paths are compensation-safe and test-backed.

## Findings

Severity: High

1. Durable balance source can drift after Redis loss between snapshots.
   - Current hydrate path uses `users.credits`, while runtime deductions/refunds live in Redis + credit txn rows.
   - Risk: stale balance resurrection if Redis loss occurs before snapshot catch-up.
   - Recommended remediation: evaluate stronger durable-balance sync policy (tracked in `BK-012`, `BK-018`).

Severity: Medium

1. Circuit-breaker/backpressure policy is still implicit.
   - Fail-fast behavior exists but no explicit breaker state machine/metrics.
   - Tracked remediation: `BK-011`.
2. Pool sizing policy is configured but not yet benchmark-driven by environment profile docs.
   - Tracked remediation: `BK-010`.
3. High-concurrency envelope is not yet benchmarked with dedicated harness.
   - Tracked remediation: `BK-009`, `BK-001`.

## Action Items and Mapping

- `BK-010`: connection pooling and lifecycle tuning
- `BK-011`: breaker/backpressure controls
- `BK-012`: UoW and rollback audit for durable-balance correctness
- `BK-009`: load/stress harness and saturation behavior
- `BK-001`: evidence-based capacity model
- `BK-018`: consistency-pattern evaluation for write paths

## Conclusion

For RFC-0000 scope, this implementation is at a high quality bar with clear tradeoffs documented and an actionable hardening backlog. No critical correctness defect was identified in currently implemented and tested request flows.
