# Solution 0 Execution Model

Date: 2026-02-15

## Goal

Deliver the spec-faithful baseline with production-grade engineering practices:
correctness first, strict typing, deterministic tests, and operability.

## Architecture Guardrails

- API: FastAPI
- Queue: Celery via Redis broker/result
- Storage: Postgres source of truth + Redis working set
- Billing gate: Redis Lua script (atomic check/deduct/concurrency/idempotency)
- Recovery: reaper process for orphan deductions and stuck tasks

## Invariants (Must Never Break)

1. No task is accepted unless credits are sufficient.
2. A user is never charged twice for one idempotency key.
3. Terminal worker failure results in credit refund.
4. Cancel of cancellable task refunds exactly task cost once.
5. System degrades with explicit 503/controlled behavior, not silent corruption.

## Type Safety Strategy

- Pydantic v2 models for every external request/response payload.
- Domain-level typed structures (`TypedDict`, dataclasses, enums).
- `mypy --strict` as merge gate.
- No `Any` in billing/auth/worker state paths.

## TDD Strategy

- Unit tests own domain logic and Lua contract behavior.
- Integration tests own persistence + queue semantics.
- Fault tests own degradation claims from RFC.
- E2E test owns demo flow.

## Card-to-Subsystem Mapping

- `P0-001`: tooling + quality gate bootstrap
- `P0-002`: schema/index/migration + seed fidelity
- `P0-003`: auth cache and credit Lua gate
- `P0-004`: API contract + input/output typing
- `P0-005`: worker execution + cancel + reaper recovery
- `P0-006`: logs/metrics/dashboard wiring
- `P0-007`: full test matrix and fault drills
- `P0-008`: demo + release gate verification
