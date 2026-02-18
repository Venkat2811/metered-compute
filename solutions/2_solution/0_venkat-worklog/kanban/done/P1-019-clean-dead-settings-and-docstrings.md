# P1-019: Clean dead settings, imports, and docstrings

Priority: P1
Status: done
Depends on: P1-015, P1-016

## Objective

Remove Sol 1 settings fields, dead imports, and fix docstrings that reference stream/Lua/Celery patterns no longer used in Sol 2.

## Why

Settings like `redis_tasks_stream_key`, `stream_consumer_group`, `stream_claim_*`, `reaper_*` are Sol 1 artifacts. Module docstrings referencing "Redis Streams" or "Lua mega-script" are misleading in Sol 2.

## Scope

- `src/solution2/core/settings.py`
  - Remove stream-related fields: `redis_tasks_stream_key`, `stream_consumer_group`, `stream_claim_min_idle_ms`, `stream_batch_size`
  - Remove reaper fields: `reaper_interval_seconds`, `reaper_max_age_seconds`
  - Remove Celery fields if any remain
  - Add any missing Sol 2 fields (e.g., `reservation_ttl_seconds`, `watchdog_interval_seconds`)
- Module docstrings across `src/solution2/` — update references from "Redis Streams" to "RabbitMQ"
- Dead imports cleanup after P1-015 and P1-016

## Checklist

- [x] No stream/reaper/celery settings remain
- [x] Sol 2 settings present (reservation_ttl, watchdog_interval, rabbitmq_url, etc.)
- [x] Docstrings reference correct architecture (CQRS, RabbitMQ, outbox)
- [x] No unused imports
- [x] `ruff check` passes
- [ ] `mypy --strict` passes (`src/tests` baseline still has pre-existing strict errors in `services/rabbitmq.py` + outbox relay tests)

## Validation

- `ruff check src/solution2/`
- `mypy --strict src/solution2/`
- `uv run pytest tests/ -q`
