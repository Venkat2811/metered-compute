# 3_solution: Financial Core

In-progress implementation for the TigerBeetle + Redpanda + CQRS solution track.

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

Optional full proof command for the current scoped implementation:

```bash
make prove
# alias:
make full-check
```

Current proof scope is intentionally narrower than the final RFC scope:

- quality gates for `src/solution3` and `tests_bootstrap`
- coverage for the currently implemented API, billing, and worker-runtime seams
- compose startup + `/health` and `/ready` smoke checks
- running-stack OAuth + submit/poll/cancel/admin-RBAC integration checks
- live infra integration for outbox relay -> Redpanda -> dispatcher -> RabbitMQ publish
- live infra integration for RabbitMQ preloaded-header preference and cold-fallback routing
- live infra integration for submit -> TigerBeetle reserve -> outbox relay -> Redpanda -> dispatcher -> RabbitMQ -> worker -> poll completed
- bootstrap demo smoke
- one failure-path script check for readiness timeout

## Current Status

This directory has completed `P0-004` and is ready to move into `P0-005`.

What is already real:

- standalone `solution3` Python package under `src/solution3`
- reviewer-first Docker Compose stack with Postgres, Redis, RabbitMQ, Hydra, Redpanda, TigerBeetle, API, and worker-shaped processes
- FastAPI app with `/health`, `/ready`, `/v1/oauth/token`, `/v1/task`, `/v1/poll`, `/v1/task/{id}/cancel`, and `/v1/admin/credits`
- enum-driven SQL migrations plus a host-side `make migrate` / `scripts/migrate.sh` path
- command-store repository helpers for `task_commands`, `outbox_events`, and guarded cancel updates
- Hydra-backed JWT verification and scope-aware route protection
- Redis hot-path task cache for poll reads
- TigerBeetle billing primitives for account bootstrap and pending/post/void transfer lifecycle
- TigerBeetle bootstrap on API startup for the platform accounts and seeded users
- real outbox relay process that drains unpublished command events into Redpanda with publish-after-ack semantics
- real dispatcher process that consumes `tasks.requested` from Redpanda and republishes to RabbitMQ header exchanges
- real worker consume loop over RabbitMQ cold + warm queues with guarded running transition and terminal completion updates
- end-to-end TigerBeetle reserve/post/void handling on submit, cancel, success, and failure paths
- worker runtime seams for cold-start model loading, warm-model registration, hot-queue activation, and terminal completion updates
- outbox relay and dispatcher contract seams with unit-tested publish/flush behavior
- worker-shaped entrypoints for `dispatcher`, `projector`, `reconciler`, `worker`, `watchdog`, and `webhook-worker`
- isolated test suite in `tests_bootstrap/` covering unit, integration, e2e, and fault slices for the implemented scope

What is not implemented yet:

- projector and rebuild flows from the event backbone
- full CQRS query projection pipeline
- successful admin top-up path
- scenario harness and load profile

Those land in `P0-004` onward. The kanban reflects the real ship order.

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
|-- api
|   |-- admin_routes.py
|   |-- auth_routes.py
|   |-- error_responses.py
|   |-- paths.py
|   |-- task_read_routes.py
|   `-- task_write_routes.py
|-- app.py
|-- constants.py
|-- core
|   |-- runtime.py
|   `-- settings.py
|-- db
|   |-- migrate.py
|   |-- migrations
|   `-- repository.py
|-- main.py
|-- models
|   |-- domain.py
|   `-- schemas.py
|-- services
|   `-- auth.py
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
make migrate
pytest tests_bootstrap/unit
pytest tests_bootstrap/integration -m integration
make up
make wait-ready
make demo
make down
```
