# 3_solution: Financial Core

Implemented TigerBeetle + Redpanda + CQRS solution track.

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
- live infra integration for `poll` query-view fallback after evicting the Redis task key
- live infra integration for projection reset -> Redpanda replay rebuild -> `poll` restored from rebuilt query state
- live infra integration for reconciler repair after command/query rollback against TigerBeetle posted and voided terminal states
- live infra integration for webhook callback success and durable dead-letter capture after bounded delivery retries
- deterministic scenario harness covering health, auth, submit/poll, idempotency, admin top-up, and cancel-while-paused flows
- bootstrap demo smoke
- one failure-path script check for readiness timeout

## Current Status

This directory has completed `P0-006`. The projector, rebuild tooling, TigerBeetle-aware reconciler, webhook delivery, Prometheus metrics, Grafana dashboard, alert rules, deterministic scenario harness, load harness, and capacity model are all real, and `make prove` verifies them from a clean state.

What is already real:

- standalone `solution3` Python package under `src/solution3`
- reviewer-first Docker Compose stack with Postgres, Redis, RabbitMQ, Hydra, Redpanda, TigerBeetle, a one-shot schema migrator, API, and worker-shaped processes
- FastAPI app with `/health`, `/ready`, `/v1/oauth/token`, `/v1/task`, `/v1/poll`, `/v1/task/{id}/cancel`, and `/v1/admin/credits`
- enum-driven SQL migrations plus a host-side `make migrate` / `scripts/migrate.sh` path
- command-store repository helpers for `task_commands`, `outbox_events`, and guarded cancel updates
- Hydra-backed JWT verification and scope-aware route protection
- Redis hot-path task cache for poll reads
- TigerBeetle billing primitives for account bootstrap and pending/post/void transfer lifecycle
- TigerBeetle bootstrap on API startup for the platform accounts and seeded users
- admin credit top-up path via active API-key lookup, TigerBeetle direct transfer, and command-store outbox event emission
- real outbox relay process that drains unpublished command events into Redpanda with publish-after-ack semantics
- real dispatcher process that consumes `tasks.requested` from Redpanda and republishes to RabbitMQ header exchanges
- real worker consume loop over RabbitMQ cold + warm queues with guarded running transition and terminal completion updates
- end-to-end TigerBeetle reserve/post/void handling on submit, cancel, success, and failure paths
- worker runtime seams for cold-start model loading, warm-model registration, hot-queue activation, and terminal completion updates
- real projector process that consumes Redpanda task events into `query.task_query_view`, inbox dedup, checkpoints, and Redis write-through
- live poll fallback from `query.task_query_view` when the Redis task key is missing
- rebuild tooling that can restore the projection from SQL or replay the Redpanda task log from offset 0
- real reconciler path for stale `RESERVED` tasks: one-shot or looped scan, guarded transition to `EXPIRED`, Redis hot-path update, active-slot release, and `tasks.expired` outbox emission
- explicit TigerBeetle drift alignment for stale `RESERVED` tasks whose pending transfer has already been posted or voided, with command/query/outbox correction back to terminal state
- real webhook worker that consumes terminal Redpanda events, looks up `callback_url` from the command store, retries delivery with bounded exponential backoff, and persists exhausted deliveries into `cmd.webhook_dead_letters`
- outbox relay and dispatcher contract seams with unit-tested publish/flush behavior
- worker-shaped entrypoints for `dispatcher`, `projector`, `reconciler`, `worker`, `watchdog`, and `webhook-worker`
- isolated test suite in `tests_bootstrap/` covering unit, integration, e2e, and fault slices for the implemented scope
- deterministic `scripts/run_scenarios.py` harness that writes JSON evidence and exercises the shipped HTTP flows against a live stack
- `scripts/load_harness.py` plus `scripts/capacity_model.py` for lightweight measured throughput and derived monthly projections
- Prometheus metrics for API, worker, dispatcher, outbox relay, projector, reconciler, webhook worker, and watchdog processes
- non-placeholder Grafana dashboard plus alert rules for the shipped runtime signals

What is not implemented yet:

- RFC-only optional extensions: OTel/Tempo runtime instrumentation, OpenSearch, and ClickHouse analytics

Those remain RFC-only options and are not required to consider the coded Solution 3 track complete. The kanban reflects the real ship order.

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
|-- observability
|   `-- metrics.py
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
make rebuild-query
make replay-query
make scenarios
make loadtest
make capacity-model
pytest tests_bootstrap/unit
pytest tests_bootstrap/integration -m integration
make up
make wait-ready
make demo
make down
```
