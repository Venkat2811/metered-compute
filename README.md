# metered-compute

Reference architectures for authenticated, credit-metered async compute.

## The Problem

Every company running LLM inference, image/video/audio generation, sandboxed code execution, or RL training loops faces the same infrastructure challenge: **metering compute that is expensive, spiky, and asynchronous**.

GPU and CPU time must be gated per-user, billed accurately, and released immediately on failure. A single lost credit deduction or double-charge erodes trust. A stuck task that holds credits forever burns runway. The system must handle:

- **Pre-flight credit checks** — reject work before it starts if the user can't pay
- **Atomic billing** — deduct, execute, and finalize as one logical unit, even across crashes
- **Cancellation at any point** — release held credits whether the task is queued, running, or finishing
- **Concurrent users with different tiers** — rate limits, model access, and concurrency caps per plan
- **Spiky traffic** — batch submits, long-running GPU jobs, and bursty API calls from agents and pipelines

This repo implements five progressively sophisticated solutions to this problem, from a Celery baseline to TigerBeetle + Restate durable execution.

## Solutions

| Track | Thesis | Stack | When to use |
|-------|--------|-------|-------------|
| [Sol 0](solutions/0_solution/) | Pragmatic baseline | Celery + Redis + Postgres | Quick prototype, familiar stack |
| [Sol 1](solutions/1_solution/) | Redis-native engine | JWT + Redis Streams + Lua | Low-latency, zero-PG hot path |
| [Sol 2](solutions/2_solution/) | Service-grade platform | CQRS + RabbitMQ + reservations | Enterprise messaging, audit trail |
| [Sol 3](solutions/3_solution/) | Financial core | TigerBeetle + Redpanda + CQRS | Jepsen-verified billing, event sourcing |
| [Sol 4](solutions/4_solution/) | TB + Restate showcase | TigerBeetle + Restate | Minimal code, durable execution |

Each solution is independently runnable with full test suites, scenario harnesses, load tests, fault tests, observability (Prometheus + Grafana), and a demo script.

## Quick Start

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
`-- solutions/
    |-- README.md                        # solution matrix and full comparison
    |-- 0_solution/                      # Celery baseline
    |-- 1_solution/                      # Redis Streams native
    |-- 2_solution/                      # CQRS + RabbitMQ
    |-- 3_solution/                      # TigerBeetle + Redpanda
    `-- 4_solution/                      # TigerBeetle + Restate
```

## Read Order

1. This README (problem statement)
2. `solutions/README.md` (solution matrix and full comparison)
3. `.0_agentic_engineering/0_rfcs/` (architecture deep-dives per track)
4. `solutions/<n>_solution/README.md` (implementation details)

## Development

Each solution ships:
- `make quality` — ruff, mypy, bandit, pip-audit, detect-secrets, radon
- `make coverage` — pytest with module-level coverage floors
- `make prove` — full quality + coverage + compose rebuild + integration + fault + scenarios
- `docker compose up --build -d` — complete stack
- `bash scripts/demo.sh` — end-to-end walkthrough
