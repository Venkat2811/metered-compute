# 2_solution

JWT/OAuth and Redis fast-path evolution:
- OAuth token service + JWT auth for task/poll
- Redis Streams queue + Lua atomic billing/check/enqueue
- Postgres as control/audit plane

Primary design doc:
- `../0_0_rfcs/RFC-0003-2-solution-jwt-redis-fastpath.md`
