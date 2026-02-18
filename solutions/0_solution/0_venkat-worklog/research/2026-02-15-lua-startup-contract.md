# Lua Startup Contract (Solution 0)

Date: 2026-02-15  
Scope: `0_solution` admission and active-counter scripts

## Contract

1. API startup must load both Redis Lua scripts before accepting requests:
   - `ADMISSION_LUA`
   - `DECR_ACTIVE_CLAMP_LUA`
2. Runtime keeps script SHAs in memory (`RuntimeState`).
3. Readiness (`/ready`) is `503` unless:
   - dependency probes pass
   - worker connectivity probe passes
   - `SCRIPT EXISTS` confirms both SHAs are available in Redis.
4. If Redis script cache is lost after restart/failover:
   - first `EVALSHA` can fail with `NoScript`
   - service must self-heal by `SCRIPT LOAD` + retry.

## Operational Behavior

- Expected post-restart sequence:
  1. `redis` comes back with empty script cache
  2. API `/ready` returns `503` until scripts are visible
  3. first submit path can trigger script reload fallback
  4. subsequent requests use refreshed SHA

- Failure policy:
  - Redis unavailable: return `503 SERVICE_DEGRADED`, never `500` in submit/poll paths.
  - Script-missing only: auto-reload and continue.

## Test Evidence

- Unit:
  - `tests/unit/test_billing_service.py::test_run_admission_gate_recovers_from_noscript`
  - `tests/unit/test_billing_service.py::test_decrement_active_counter_recovers_from_noscript`
- Fault:
  - `tests/fault/test_readiness_degradation.py::test_ready_degrades_when_redis_is_down_and_recovers`
    - includes assertion that post-restart submit is in `{201, 402, 429}` (no script-cache 500).
- Readiness path:
  - `src/solution0/app.py` checks `script_exists(...)` in `/ready`.
