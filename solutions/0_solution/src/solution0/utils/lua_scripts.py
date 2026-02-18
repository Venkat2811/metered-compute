from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ADMISSION_LUA = """
-- KEYS[1] = credits:{user_id}
-- KEYS[2] = idem:{user_id}:{idempotency_key}
-- KEYS[3] = active:{user_id}
-- ARGV[1] = cost
-- ARGV[2] = task_id
-- ARGV[3] = max_concurrent
-- ARGV[4] = idempotency_ttl

local existing = redis.call('GET', KEYS[2])
if existing then
  return cjson.encode({ok=false, reason='IDEMPOTENT', task_id=existing})
end

local active = tonumber(redis.call('GET', KEYS[3]) or '0')
if active >= tonumber(ARGV[3]) then
  return cjson.encode({ok=false, reason='CONCURRENCY'})
end

local bal = tonumber(redis.call('GET', KEYS[1]))
if bal == nil then
  return cjson.encode({ok=false, reason='CACHE_MISS'})
end
if bal < tonumber(ARGV[1]) then
  return cjson.encode({ok=false, reason='INSUFFICIENT'})
end

redis.call('DECRBY', KEYS[1], ARGV[1])
redis.call('SETEX', KEYS[2], ARGV[4], ARGV[2])
redis.call('INCR', KEYS[3])
redis.call('SADD', 'credits:dirty', KEYS[1])

return cjson.encode({ok=true, reason='OK'})
"""


DECR_ACTIVE_CLAMP_LUA = """
-- KEYS[1] = active:{user_id}
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current <= 0 then
  redis.call('SET', KEYS[1], 0)
  return 0
end
return redis.call('DECR', KEYS[1])
"""


@dataclass(frozen=True)
class LuaAdmissionResult:
    ok: bool
    reason: str
    task_id: str | None


def parse_lua_result(payload: str) -> LuaAdmissionResult:
    """Decode Lua JSON payload into a typed result."""

    loaded: dict[str, Any] = json.loads(payload)
    return LuaAdmissionResult(
        ok=bool(loaded.get("ok", False)),
        reason=str(loaded.get("reason", "UNKNOWN")),
        task_id=str(loaded["task_id"]) if loaded.get("task_id") is not None else None,
    )
