# BK-025 — P2: Connection Pool and Timeout Hardening

Priority: P2 (nice-to-have, no production risk at compose scale)
Status: done
Solution: 0_solution

## Context

Core timeout protection is already shipped (statement_timeout=50ms, socket_timeout=0.05s, command_timeout=0.1s, retry jitter). These are residual gaps that matter at scale but are non-issues for compose-level demo.

## Gap 1: asyncpg pool.acquire() timeout

**Current:** `asyncpg.create_pool()` has no `timeout` kwarg on `pool.acquire()`. Under pool exhaustion, acquire blocks indefinitely.

**Fix:** Wrap `pool.acquire()` calls with `asyncio.wait_for(pool.acquire(), timeout=2.0)` or use asyncpg's `connection_class` with a custom acquire wrapper.

**Risk:** Low — compose runs 1-2 API workers against a pool of 10; exhaustion is unlikely.

## Gap 2: Redis max_connections limit

**Current:** `redis.asyncio.Redis` clients don't set `max_connections` on the connection pool. Under sustained load, unbounded connection growth is possible.

**Fix:** Add `max_connections=50` (or similar) to Redis client construction in `dependencies.py`.

**Risk:** Low — compose-scale traffic won't exhaust Redis connections.

## Gap 3: Readiness probe Redis socket_connect_timeout

**Current:** `dependencies.py:78` — the readiness Redis ping uses the shared client which has `socket_timeout` but the initial TCP connect has no explicit `socket_connect_timeout`.

**Fix:** Add `socket_connect_timeout=0.05` to Redis client construction.

**Risk:** Minimal — readiness probe could hang on TCP SYN to an unresponsive Redis, but k8s/compose health check timeout covers this.

## Definition of Done

- [x] `pool.acquire()` wrapped with timeout
- [x] Redis clients have `max_connections` set
- [x] Redis clients have `socket_connect_timeout` set
- [x] Existing tests pass
- [x] No new dependencies
