# 3_solution: Financial Core

Bootstrap implementation for the TigerBeetle + Redpanda + CQRS solution track.

Compose project name: `mc-solution3` (set in `compose.yaml`).

Primary RFC:

- `../0_1_rfcs/RFC-0003-3-solution-financial-core/README.md`

## Setup, Run, Demo (Reviewer First)

1. Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv sync --dev
```

2. Run

```bash
docker compose up --build -d
docker compose ps
```

3. See demo

```bash
source .venv/bin/activate
python ./utils/demo.py
```

Optional full proof command for the current bootstrap scope:

```bash
make prove
# alias:
make full-check
```

Current proof scope is intentionally narrow:

- quality gates for `src/solution3` and `tests_bootstrap`
- coverage for bootstrap code
- compose startup + `/health` and `/ready` smoke checks
- bootstrap demo smoke
- one failure-path script check for readiness timeout

## Current Status

This directory is in `P0-001` bootstrap mode.

What is already real:

- standalone `solution3` Python package under `src/solution3`
- reviewer-first Docker Compose stack with Postgres, Redis, RabbitMQ, Hydra, Redpanda, TigerBeetle, API, and worker-shaped processes
- minimal FastAPI app with `/health` and `/ready`
- bootstrap worker entrypoints for `dispatcher`, `projector`, `reconciler`, `worker`, `watchdog`, and `webhook-worker`
- isolated bootstrap test suite in `tests_bootstrap/`

What is not implemented yet:

- submit, poll, cancel, admin credits APIs
- TigerBeetle billing integration
- Redpanda outbox, dispatcher, projector, and rebuild flows
- CQRS query model
- scenario harness and load profile

Those land in `P0-002` onward. The kanban reflects the real ship order.

## Lay Of The Land

Repository shape:

```text
.
|-- worklog
|   `-- kanban
|-- docker
|   |-- api
|   |-- dispatcher
|   |-- postgres
|   |-- projector
|   |-- reconciler
|   |-- webhook_worker
|   `-- worker
|-- monitoring
|-- scripts
|-- src
|   `-- solution3
|-- tests_bootstrap
`-- utils
```

Source package shape:

```text
src/solution3
|-- app.py
|-- constants.py
|-- core
|   |-- runtime.py
|   `-- settings.py
|-- main.py
|-- models
|   `-- schemas.py
|-- utils
|   `-- logging.py
`-- workers
    |-- _bootstrap_worker.py
    |-- dispatcher.py
    |-- outbox_relay.py
    |-- projector.py
    |-- reconciler.py
    |-- watchdog.py
    |-- webhook_dispatcher.py
    `-- worker.py
```

## Useful Commands

```bash
make help
make quality
make coverage
make up
make wait-ready
make demo
make down
```
