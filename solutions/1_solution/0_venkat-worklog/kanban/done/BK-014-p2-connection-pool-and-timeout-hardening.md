# BK-014 — P2: Connection Pool and Timeout Hardening

Priority: P2 (nice-to-have, no production risk at compose scale)
Status: done
Solution: 1_solution

## Context

Core timeout protection is already shipped (statement_timeout=50ms, socket_timeout=0.05s, command_timeout=0.1s, retry jitter). These are residual gaps that matter at scale but are non-issues for compose-level demo.

## Gap 1: asyncpg pool.acquire() timeout

**Current:** `asyncpg.create_pool()` has no `timeout` kwarg on `pool.acquire()`. Under pool exhaustion, acquire blocks indefinitely.

**Fix:** Wrap `pool.acquire()` calls with `asyncio.wait_for(pool.acquire(), timeout=2.0)` or use asyncpg's `connection_class` with a custom acquire wrapper.

**Risk:** Low — compose runs 1-2 API workers against a pool of 10; exhaustion is unlikely.

## Gap 2: Redis max_connections limit

**Current:** `redis.asyncio.Redis` clients don't set `max_connections` on the connection pool. Under sustained load, unbounded connection growth is possible.

**Fix:** Add `max_connections=50` (or similar) to Redis client construction in `dependencies.py` / `app.py`.

**Risk:** Low — compose-scale traffic won't exhaust Redis connections.

## Gap 3: httpx connection pool Limits

**Current:** `httpx.AsyncClient` used for webhook dispatcher and OAuth/Hydra calls has no explicit `limits=httpx.Limits(...)` configured. Default is 100 connections per host, which is fine, but making it explicit improves observability and prevents surprise under load.

**Fix:** Add `limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)` to httpx client construction.

**Risk:** Minimal — defaults are reasonable; this is a clarity improvement.

## Definition of Done

- [x] `pool.acquire()` wrapped with timeout
- [x] Redis clients have `max_connections` set
- [x] httpx clients have explicit `Limits` configured
- [x] Existing tests pass
- [x] No new dependencies
