# 5_solution: TigerBeetle + Restate Showcase

Minimal implementation (~700 LOC) demonstrating that **TigerBeetle** (double-entry accounting) and
**Restate** (durable execution) replace thousands of lines of application infrastructure while
providing stronger correctness guarantees.

Compose project name: `mc-solution4` (set in `compose.yaml`).

Primary RFC:

- `../0_1_rfcs/RFC-0005-5-solution-tb-restate-showcase/README.md`

## Setup, Run, Demo (Reviewer First)

1. Setup

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

2. Run

```bash
docker compose up --build -d
docker compose ps
```

3. See demo

```bash
source .venv/bin/activate
bash scripts/demo.sh
```

Optional full proof command (quality + coverage + clean compose rebuild + integration + fault + 13 scenarios + log capture):

```bash
make prove
# alias:
make full-check
```

Artifacts are written to:

- `worklog/evidence/full-check-<timestamp>/`
- test logs, scenario report, compose logs, per-service logs

Sustained load test (separate from prove — requires running stack):

```bash
make loadtest                                    # 100 RPS x 30s (default)
make loadtest LOADTEST_ARGS="--rps 200 --duration 60"  # custom
```

Report: `worklog/evidence/load/loadtest-latest.json` — includes p50/p95/p99 latencies, acceptance rate, status distribution, and poll sample.

## What TigerBeetle replaces

| Removed                              | TigerBeetle equivalent                       |
|--------------------------------------|----------------------------------------------|
| `credit_reservations` table          | Pending transfers (auto-timeout)             |
| `credit_transactions` table          | Built-in transfer log                        |
| Watchdog / reaper for expired holds  | TB auto-voids on pending transfer timeout    |
| All credit arithmetic SQL            | `debits_must_not_exceed_credits` account flag|
| Reconciler service                   | TB is source of truth — no drift possible    |

## What Restate replaces

| Removed                              | Restate equivalent                           |
|--------------------------------------|----------------------------------------------|
| `outbox_events` table                | Restate journal (durable step results)       |
| `outbox_relay` service               | Built-in retry with backoff                  |
| `inbox_events` dedup table           | Built-in idempotency per invocation          |
| Worker compensation logic            | Lifecycle replay (control plane only)        |
| Redis cache sync code                | Durable step (survives crashes)              |

## By the numbers

| Metric                | Sol 0 | Sol 2  | **Sol 5** |
|-----------------------|-------|--------|-----------|
| Python LOC            | ~800  | ~3,000 | **~700**  |
| PG tables             | 3     | 8+     | **3**     |
| Running containers    | 7     | 12     | **8 (+1 init)** |
| Outbox / relay        | No    | Yes    | **No**    |
| Billing correctness   | SQL   | SQL    | **Jepsen-verified** |
| Crash recovery        | Manual| Outbox | **Auto-replay** |

## Local Setup

Quick workflow:

```bash
make help
make quality
make coverage
```

Smoke checks:

```bash
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/ready
curl -sS http://localhost:8000/metrics | head
```

## Reproducibility Defaults (Dev Only)

Seeded users and API keys are defined in `migrations/0002_seed.sql`:

- `alice` / `sk-alice-secret-key-001` (1000 credits)
- `bob` / `sk-bob-secret-key-002` (500 credits)

Load keys into your shell when running manual curl flows:

```bash
ALICE_KEY="sk-alice-secret-key-001"
BOB_KEY="sk-bob-secret-key-002"
```

## Demo Flows

### End-to-end submit/poll/cancel

```bash
bash scripts/demo.sh
```

### Admin top-up

```bash
curl -sS -X POST http://localhost:8000/v1/admin/credits \
  -H "Authorization: Bearer sk-alice-secret-key-001" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"<alice-uuid>","amount":100}'
```

### Insufficient credits scenario

Submit tasks until credits are drained, then:

```bash
curl -sS -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer sk-bob-secret-key-002" \
  -H "Content-Type: application/json" \
  -d '{"x":1,"y":2}'
```

Expected: `402` with `Insufficient credits`.

### Scenario harness (13 scenarios, includes scope gates)

```bash
source .venv/bin/activate
python scripts/run_scenarios.py
```

This exercises:

- health/readiness checks
- auth errors, admin top-up, submit/poll lifecycle
- idempotency replay (same key → same task)
- insufficient-credit behavior
- cancel with credit refund
- ownership enforcement (bob cannot cancel alice's task)
- concurrent submits from multiple users
- Prometheus metrics availability
- demo script execution
- unsupported surface gate (`/v1/task` rejects unknown fields, `/v1/task/batch` is intentionally absent)

### Scope and compatibility contract

Solution 5 intentionally supports a narrow compatibility slice:

- API-key authentication only.
- No JWT/OAuth endpoint layer.
- No batch submit endpoint.
- No tier/model-class parameters.
- No `/task` legacy path.
- No webhook callbacks.

Unsupported surface is explicit:
- unknown request fields on `/v1/task` → `422`
- unsupported routes such as `/v1/task/batch` → `404`

## Architecture

```
Client → FastAPI (port 8000) → TigerBeetle (billing: reserve/capture/void)
                              → Postgres (task metadata, users, auth)
                              → Redis (query cache, auth cache)
                              → Restate (durable task workflow)
```

## API

| Method | Path                     | Description             |
|--------|--------------------------|-------------------------|
| POST   | `/v1/task`               | Submit task (API key)   |
| GET    | `/v1/poll?task_id=<id>`  | Poll status / result    |
| POST   | `/v1/task/{id}/cancel`   | Cancel + refund credits |
| POST   | `/v1/admin/credits`      | Admin credit topup      |
| GET    | `/health`                | Liveness                |
| GET    | `/ready`                 | Readiness (PG+Redis)    |
| GET    | `/metrics`               | Prometheus scrape       |

## Stack

- API: FastAPI (`src/solution5/app.py`)
- Compute: external FastAPI worker (`src/solution5/workers/compute_worker.py`)
- Billing: TigerBeetle double-entry engine (`src/solution5/billing.py`)
- Workflow: Restate durable control plane (`src/solution5/workflows.py`)
- Storage: Postgres (metadata), Redis (cache)
- Observability: Prometheus + Grafana + structured JSON logs
- Deployment: Docker Compose (`compose.yaml`)

## Lay Of The Land (Code Structure)

```text
.
├── worklog
│   ├── baselines
│   ├── evidence
│   └── kanban
├── migrations
├── monitoring
│   ├── grafana
│   └── prometheus
├── scripts
├── src
│   └── solution5
└── tests
    ├── integration
    └── unit
```

Source package:

```text
src/solution5/
├── __init__.py
├── app.py              FastAPI routes, auth, lifespan
├── billing.py          TigerBeetle client: reserve/capture/void/topup
├── cache.py            Redis cache helpers
├── logging.py          structlog config
├── metrics.py          Prometheus counters and histograms
├── repository.py       Postgres queries and migrations
├── settings.py         Pydantic settings
├── workflows.py        Restate durable control-plane workflow
└── workers
    ├── compute_gateway.py  External compute client used inside Restate steps
    └── compute_worker.py   Dedicated compute process with its own /metrics
```

What each module owns:

- `app.py`: ASGI factory, lifespan (PG + Redis + TB + Restate init), auth middleware, all HTTP routes
- `billing.py`: TigerBeetle account creation, pending/post/void transfers, topup, balance queries
- `workflows.py`: Restate service handler — durable task lifecycle (mark running → dispatch compute → capture → store → cache)
- `repository.py`: Postgres queries — create/get/update tasks, user lookup, migrations
- `cache.py`: Redis cache-aside for auth and task status
- `settings.py`: Pydantic settings from environment
- `logging.py`: structlog JSON configuration
- `metrics.py`: Prometheus counter/histogram definitions
- `workers/compute_gateway.py`: HTTP boundary between Restate control plane and compute plane
- `workers/compute_worker.py`: external compute plane process

## Transfer lifecycle (TigerBeetle)

```
Submit:   pending_transfer(user → escrow, timeout=300s)
Complete: post_pending_transfer(escrow → revenue)    → credits captured
Cancel:   void_pending_transfer()                     → credits returned
Expire:   auto-void after timeout                     → credits returned
```

## Workflow (Restate) — control plane only

```python
@task_service.handler()
async def execute_task(ctx, request):
    await mark_running(task_id)                          # control: idempotent
    result = await ctx.run("compute", request_compute_sync)  # data plane (external worker)
    captured = await ctx.run("capture", capture_credits) # control: journaled
    await store_result(task_id, result)                  # control: idempotent
    await update_cache(task_id, result)                  # control: idempotent
```

Restate manages **task lifecycle** (control plane), not inference (data plane).
The shipped implementation already extracts compute into a separate `compute`
service and calls it through `workers/compute_gateway.py`. That keeps the durable
workflow focused on state transitions and billing, while compute remains an
independent failure domain.

If the process crashes between any steps, Restate replays from the last journaled
step. No outbox table. No relay service. No compensation code.

## Test Gates

Quality gate (7 checks):

```bash
./scripts/quality_gate.sh
# or:
make quality
```

Runs: ruff format, ruff check, mypy --strict, bandit, pip-audit, detect-secrets, radon complexity.

Coverage gate:

```bash
./scripts/coverage_gate.sh
# or:
make coverage
```

Coverage policy:

- global floor: `35%` (app.py is a FastAPI factory — 0% in unit tests, covered by integration)
- critical module floors: `billing.py ≥ 70%`, `cache.py ≥ 80%`, `repository.py ≥ 80%`
- latest reports:
  - `worklog/baselines/coverage-latest.json`
  - `worklog/baselines/coverage-latest.xml`

Unit tests:

```bash
make test-unit
```

Integration tests (requires compose stack):

```bash
make test-integration
```

Fault tests (requires compose stack):

```bash
make test-fault
```

Scenarios (13 scenarios, requires compose stack):

```bash
make scenarios
```

## Observability

- API metrics: `http://localhost:8000/metrics`
- Compute metrics: `http://localhost:8001/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001` (default admin/admin)
