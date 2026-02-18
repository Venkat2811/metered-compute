# BK-010: Solution 1 Request Rate Limiter

Priority: P1
Status: done
Depends on: P1-021

## Objective

Track optional per-user request rate limiting as a feature-scope extension.

## Decision: Rejected for now

- The current implementation already enforces a **hard capacity control** at admission time:
  - `run_admission_gate` rejects with `reason="CONCURRENCY"` when active tasks reach per-tier `max_concurrent`.
  - This surfaces as HTTP `429` in submit handler.
- Existing behavior and tests already assert 429 behavior under concurrency saturation (`tests/unit/test_app_paths.py`, `tests/integration/test_multi_user_concurrency.py`, `tests/integration/test_oauth_jwt_flow.py`).
- The RFC/architecture posture does not require a separate request-rate SLA contract for this iteration; adding one would require:
  - new policy config,
  - token/idempotency-aware accounting windows,
  - additional metrics/event semantics,
  - and broader contract/API docs changes.
- Given assignment scope and risk, we defer the feature and keep behavior unchanged.

## Scope boundary update

- No code changes for BK-010 are being introduced in this cycle.
- This card is closed as **rejected-by-design (deferred)** pending explicit product-level requirement.

## Checklist

- [x] Evaluate whether a rate limiter is justified against current concurrency/error semantics
- [x] Document rationale against assignment and RFC behavior
- [x] Decide implementation vs deferral and record outcome
- [ ] Implement production-safe limiter (not justified in current scope)

## Acceptance Criteria

- [x] Feature scope decision is explicit and separated from bug-fix work
- [x] Existing 429/concurrency semantics remain unchanged
- [x] Kanban card moved from backlog to done with clear rationale

## Validation

- `uv run pytest -q tests/unit/test_app_paths.py tests/integration/test_multi_user_concurrency.py tests/integration/test_oauth_jwt_flow.py`
