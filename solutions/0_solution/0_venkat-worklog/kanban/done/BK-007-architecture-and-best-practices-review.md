# BK-007: Architecture and Best-Practices Review

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Run a formal architecture review and produce explicit findings that confirm Solution 0 is using each core technology correctly for the RFC scope.

## Checklist

- [x] Async runtime and web serving review
  - [x] verify FastAPI + Uvicorn usage, blocking-call boundaries, and pool sizing assumptions
  - [x] verify API lifecycle/resource initialization and shutdown behavior
- [x] Postgres review
  - [x] verify schema evolution approach and migration safety
  - [x] verify index strategy with query-plan evidence for hot paths
  - [x] verify transaction boundaries and consistency guarantees
- [x] Redis review
  - [x] verify key design, TTL policy, memory behavior, and restart recovery
  - [x] verify cache-aside and dirty-snapshot lifecycle
- [x] Celery review
  - [x] verify queue topology, retry semantics, revoke/cancel behavior, and broker failure handling
  - [x] verify idempotency and at-least-once implications on billing correctness
- [x] Lua scripting review
  - [x] verify atomicity guarantees and script reload behavior after Redis restart
  - [x] verify script contracts and error handling semantics
- [x] Reaper review
  - [x] verify orphan/stuck-task convergence guarantees and bounded recovery
  - [x] verify no double-refund/no lost-refund invariants
- [x] Produce architecture review report with action items and severity levels

## Exit Criteria

- [x] Written review confirms or corrects each major design decision
- [x] All critical findings have tracked remediation cards
- [x] Team can state clearly why this is the highest quality bar for Solution 0 scope

## Evidence

- Review report: `../../research/2026-02-15-architecture-best-practices-review.md`
- Related lock/transaction evidence: `../../research/2026-02-15-transaction-lock-review.md`
- Remediation mapping maintained in backlog cards:
  - `BK-010`, `BK-011`, `BK-012`, `BK-009`, `BK-001`, `BK-018`
