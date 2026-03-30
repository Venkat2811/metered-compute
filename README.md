# metered-compute

Reference architectures for authenticated, credit-metered async compute — inference, tool execution, and RL task APIs.

Five progressive implementations from Celery baseline to TigerBeetle + Restate.

## Solutions

| Track | Thesis | Stack | When to use |
|-------|--------|-------|-------------|
| [Sol 0](solutions/0_solution/) | Pragmatic baseline | Celery + Redis + Postgres | Quick prototype, familiar stack |
| [Sol 1](solutions/1_solution/) | Redis-native engine | JWT + Redis Streams + Lua | Low-latency, stream-native billing |
| [Sol 2](solutions/2_solution/) | Service-grade platform | CQRS + RabbitMQ + reservations | Enterprise messaging, audit trail |
| [Sol 3](solutions/3_solution/) | Financial core | TigerBeetle + Redpanda + CQRS | Jepsen-verified billing, event sourcing |
| [Sol 4](solutions/4_solution/) | TB + Restate showcase | TigerBeetle + Restate | Minimal code, durable execution |

## Quick Start

Pick a solution and run:

```bash
cd solutions/1_solution      # or any solution
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
docker compose up --build -d
bash scripts/demo.sh
```

## Directory Layout

```text
metered-compute/
|-- .0_agentic_engineering/
|   |-- 0_rfcs/                          # architecture RFCs per solution
|   `-- 1_worklog/0_Venkat2811/kanban/   # consolidated kanban board
|-- solutions/
|   |-- README.md                        # solution matrix and guidance
|   |-- 0_solution/                      # Celery baseline
|   |-- 1_solution/                      # Redis Streams native
|   |-- 2_solution/                      # CQRS + RabbitMQ
|   |-- 3_solution/                      # TigerBeetle + Redpanda
|   `-- 4_solution/                      # TigerBeetle + Restate
`-- LICENSE
```

## Read Order

1. `solutions/README.md` (solution matrix)
2. `.0_agentic_engineering/0_rfcs/` (architecture docs per track)
3. Corresponding `solutions/<n>_solution/README.md`

## Development

Built using agentic engineering — Claude Code for planning, Codex CLI for implementation, Cursor as IDE.

Each solution has:
- `make quality` — ruff, mypy, bandit, pip-audit, detect-secrets, radon
- `make coverage` — pytest with module-level coverage floors
- `make prove` — full quality + coverage + compose rebuild + integration + fault + scenarios
- `docker compose up --build -d` — complete stack
- `bash scripts/demo.sh` — end-to-end walkthrough
