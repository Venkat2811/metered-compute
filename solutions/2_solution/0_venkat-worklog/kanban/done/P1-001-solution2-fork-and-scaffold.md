# P1-001: Solution2 Fork + Scaffold Foundation

Priority: P1
Status: done
Depends on: 9a1e13c (solution2 checkpoint start)

## Objective

Create the baseline Sol2 scaffold by fork-copying `1_solution`, introducing `solution2` package naming, adding RabbitMQ in compose, and removing Sol1 stream/reaper assumptions.

## Scope (Milestone 0)

- Copy `1_solution` → `2_solution` and perform rename `solution1` → `solution2` in code, compose, and configs.
- Add RabbitMQ service + `RABBITMQ_URL` default and settings.
- Remove Sol1 stream/reaper services/modules and replace with RabbitMQ-aware service stubs (`worker`, `outbox-relay`, `projector`, `watchdog`, `webhook-worker`).
- Add cmd/query schema migrations:
  - `0010_create_cmd_schema.sql`
  - `0011_create_query_schema.sql`
- Add venv/compose startup sanity checks for `postgres`, `redis`, and `rabbitmq`.

## Checklist

- [x] `cp -a` scaffold completed with renamed package path/imports (`solution1` → `solution2`).
- [x] `compose.yaml` includes `rabbitmq` and health check.
- [x] `.env.dev.defaults` includes `RABBITMQ_URL`.
- [x] `core/settings.py` includes `rabbitmq_url`.
- [x] Worker container no longer boots `solution2.workers.stream_worker` (Sol2 worker stub entrypoint in place).
- [x] Lua-related files/modules removed from Sol2 scaffold.
- [x] Removed legacy stream/reaper docker-only scaffold (`docker/reaper/Dockerfile`).
- [x] Old stream/reaper docker/services retired; new RabbitMQ-oriented service stubs present.
- [x] `0010_create_cmd_schema.sql` and `0011_create_query_schema.sql` added.
- [x] README reflects Sol2 stack and bootstrap commands.

## Acceptance Criteria

- `git diff` on card shows only Sol2 scaffolding and no Sol1 stream/reaper assumptions.
- `docker compose up -d postgres redis rabbitmq` reaches healthy state.
- Moved card remains in `done` after checks are reproducibly green.

## Validation

- `rg -n "solution1|redis streams|stream_worker|reaper|XREADGROUP" solutions/2_solution`
- `uv run ruff check src tests` (scaffold paths only)
- `docker compose up -d postgres redis rabbitmq`
- `docker compose ps`
- `docker compose down -v`
