# BK-015: Lua Bootstrap and Redis Startup Contract

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Make Redis Lua bootstrap guarantees explicit across startup, restart, and failover so admission behavior is deterministic from first request.

## Checklist

- [x] Define startup contract for script loading and readiness gating
- [x] Add health/readiness probe for script availability (`SCRIPT EXISTS` or equivalent)
- [x] Add tests for Redis restart and script-cache-loss convergence
- [x] Document operational behavior and failure fallback expectations

## Exit Criteria

- [x] Script availability guarantees are explicit and test-backed
- [x] No first-request surprises after Redis restart or script cache loss
- [x] Lua bootstrap behavior is documented for operators

## Evidence

- Readiness script probe: `src/solution0/app.py` (`/ready` with `script_exists`)
- NoScript reload handling:
  - `src/solution0/services/billing.py`
  - `tests/unit/test_billing_service.py`
- Redis restart/script-cache-loss fault coverage:
  - `tests/fault/test_readiness_degradation.py`
- Operator doc:
  - `../../research/2026-02-15-lua-startup-contract.md`
