# P0-014: Fix poll path — query view + cmd join read model

Priority: P0
Status: done
Depends on: P0-011

## Objective

Rewrite the poll path to use Sol 2 CQRS read model: Redis `task:{id}` → `query.task_query_view` → `cmd.task_commands` join, replacing the current Sol 1 two-key + old-table pattern.

## Why

Current poll is architecturally wrong:
1. `_poll_from_result_cache()` reads `result_cache_key` — Sol 1 pattern
2. `_poll_from_task_state()` reads `task_state_key` with `xlen`/`llen` queue depth — Sol 1 stream pattern
3. `_poll_from_db()` queries old `tasks` table — Sol 1 table
4. Queue depth uses `redis_tasks_stream_key` (Redis Streams) — Sol 2 uses RabbitMQ

## Current (broken)

```
poll:
  1. GET result_cache_key (result:{task_id})      # Sol 1
  2. GET task_state_key (task:{task_id})           # correct key name, wrong content model
     + XLEN/LLEN redis_tasks_stream_key            # Sol 1 stream
  3. SELECT * FROM tasks WHERE task_id = $1        # Sol 1 table
```

## Target (per RFC-0002)

```
poll:
  1. HGETALL task:{task_id} from Redis             # single key, full state
     - If found with terminal status → return immediately
     - If found with PENDING/RUNNING → return with position estimate
  2. SELECT * FROM query.task_query_view            # projected read model
     WHERE task_id = $1
     - If found → return (projector has materialized it)
  3. SELECT * FROM cmd.task_commands                # command source of truth
     WHERE task_id = $1
     - Fallback for projection lag
  Queue depth: RabbitMQ management API or cached queue length (not xlen/llen)
```

## Scope

- `src/solution2/api/task_read_routes.py`
  - Rewrite poll handler to use three-tier: Redis task:{id} → query view → cmd join
  - Remove `_poll_from_result_cache()` (result_cache_key pattern)
  - Remove `_poll_from_task_state()` xlen/llen queue depth
  - Add query view lookup via new repository function
  - Add cmd.task_commands fallback
  - Queue position: either RabbitMQ API or "unknown" (not Redis stream length)
- `src/solution2/db/repository.py`
  - Add `get_task_from_query_view(pool, task_id)` if not exists
  - Ensure `get_task_command(pool, task_id)` exists for fallback

## Checklist

- [x] Redis task:{id} is first lookup (single HGETALL)
- [x] query.task_query_view is second lookup
- [x] cmd.task_commands is third lookup (fallback)
- [x] No result_cache_key usage
- [x] No xlen/llen on Redis stream keys
- [x] Queue position from RabbitMQ or omitted (not stream length)
- [x] Response shape matches RFC-0002 poll contract
- [x] 404 when task doesn't exist in any tier

## Validation

- `uv run pytest tests/unit/test_poll_path.py -q`
- `uv run pytest tests/integration/test_poll_flow.py -q`
- Manual: submit → poll shows PENDING → execute → poll shows COMPLETED

## Acceptance Criteria

- Poll reads from CQRS query side, never from command tables on happy path
- Fallback to cmd table is transparent to caller
- No Sol 1 key patterns in poll code path
