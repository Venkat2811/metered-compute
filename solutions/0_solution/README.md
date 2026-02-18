# 0_solution: Celery Baseline

Pragmatic production baseline for the metered-compute API assignment.

Compose project name: `mc-solution0` (set in `compose.yaml`).

Primary RFC:

- `../0_1_rfcs/RFC-0000-0-solution-celery-baseline/README.md`

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

Optional full proof command (quality + coverage + clean compose rebuild + integration/e2e/fault + scenarios + log capture):

```bash
make prove
# alias:
make full-check
```

Artifacts are written to:

- `worklog/evidence/full-check-<timestamp>/`
- test logs, scenario report, compose logs, per-service logs, metrics snapshots

Sustained load test (separate from prove — requires running stack):

```bash
make loadtest                                    # 100 RPS x 30s (default)
make loadtest LOADTEST_ARGS="--rps 200 --duration 60"  # custom
```

Report: `worklog/evidence/load/loadtest-latest.json` — includes p50/p95/p99 latencies, acceptance rate, status distribution, and poll sample.

## Local Setup

Quick workflow:

```bash
make help
make quality
make coverage
make docker-lock
make smell
```

Smoke checks:

```bash
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/ready
curl -sS http://localhost:8000/metrics | head
```

## Reproducibility Defaults (Dev Only)

Seeded API keys are sourced from `.env.dev.defaults` and rendered into migrations at runtime:

- `ADMIN_API_KEY`
- `ALICE_API_KEY`
- `BOB_API_KEY`

`AppSettings` reads `.env.dev.defaults` only when `APP_ENV=dev` (explicit environment variables still take precedence).

Load defaults into your shell when running manual curl flows:

```bash
set -a
source ./.env.dev.defaults
set +a
```

## Demo Flows

### End-to-end submit/poll

```bash
./utils/demo.sh
```

Python demo script (assignment-faithful `/task` + `/poll` flow):

```bash
source .venv/bin/activate
python ./utils/demo.py
```

### Admin top-up

```bash
curl -sS -X POST http://localhost:8000/v1/admin/credits \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${ALICE_API_KEY}\",\"delta\":100,\"reason\":\"manual_adjust\"}"
```

### Insufficient credits scenario

```bash
curl -sS -X POST http://localhost:8000/v1/admin/credits \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${BOB_API_KEY}\",\"delta\":-245,\"reason\":\"drain_for_demo\"}"

curl -sS -X POST http://localhost:8000/v1/task \
  -H "Authorization: Bearer ${BOB_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"x":1,"y":2}'
```

Expected second response: `402` with `INSUFFICIENT_CREDITS`.

### Scenario harness (12 scenarios, includes multi-user concurrency burst)

```bash
source .venv/bin/activate
python ./scripts/run_scenarios.py
```

This exercises:

- auth errors, admin top-up, submit/poll via `/v1/*` and compatibility `/task` + `/poll`
- idempotency replay and idempotency conflict
- insufficient-credit behavior
- cancel while worker is paused
- concurrent submits from multiple users with per-user concurrency enforcement
- python demo script execution itself

## Stack

- API: FastAPI (`src/solution0/main.py`)
- Worker queue: Celery + Redis broker/result backend (`src/solution0/workers/worker_tasks.py`)
- Storage: Postgres (source of truth), Redis (auth/credit/idempotency/runtime state)
- Reconciliation: background reaper (`python -m solution0.workers.reaper`)
- Observability: Prometheus + Grafana + structured JSON logs
- Deployment target for this solution: Docker Compose (`compose.yaml`)
- Runtime hardening included: pooled DB connections, transactional multi-step writes, graceful shutdown hooks

## Lay Of The Land (Code Structure)

Generated with:

```bash
LC_ALL=C tree -a -L 2 -I '__pycache__|*.pyc|.pytest_cache|.mypy_cache|.ruff_cache|.venv|postgres_data|evidence|.coverage|coverage.xml|src/mc_solution0.egg-info'
LC_ALL=C tree -a -L 2 -I '__pycache__|*.pyc' src/solution0
```

Repository shape:

```text
.
|-- worklog
|   |-- baselines
|   |-- kanban
|   `-- research
|-- docker
|   |-- api
|   |-- reaper
|   `-- worker
|-- monitoring
|   |-- grafana
|   `-- prometheus
|-- scripts
|-- src
|   `-- solution0
|-- tests
|   |-- e2e
|   |-- fault
|   |-- integration
|   `-- unit
`-- utils
```

Source package shape:

```text
src/solution0
|-- api
|   |-- admin_routes.py
|   |-- contracts.py
|   |-- paths.py
|   |-- system_routes.py
|   |-- task_read_routes.py
|   `-- task_write_routes.py
|-- app.py
|-- constants.py
|-- core
|   |-- defaults.py
|   |-- dependencies.py
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
|   |-- auth.py
|   `-- billing.py
|-- utils
|   |-- logging.py
|   |-- lua_scripts.py
|   `-- retry.py
`-- workers
    |-- celery_app.py
    |-- reaper.py
    `-- worker_tasks.py
```

What each area owns:

- `src/solution0/main.py`: ASGI entrypoint (`solution0.main:app`)
- `src/solution0/app.py`: app assembly, lifespan, shared helpers used by route modules
- `src/solution0/api/*`: HTTP contracts (`contracts.py` Protocol types) and orchestration (`submit`, `poll`, `cancel`, admin, system)
- `src/solution0/services/*`: Redis auth/cache helpers and Lua admission/compensation logic
- `src/solution0/db/*`: migrations and SQL repository functions
- `src/solution0/workers/worker_tasks.py`: Celery worker runtime + task execution state transitions
- `src/solution0/workers/reaper.py`: orphan/stuck recovery and credit snapshot flush
- `src/solution0/observability/metrics.py`: Prometheus metric definitions
- `src/solution0/utils/retry.py`: bounded async retry/backoff helper shared by API, worker, and reaper
- `scripts/*`: one-command quality/test/verification automation

## Test Gates

- Unit:

```bash
./scripts/ci_check.sh
# or:
make gate-unit
```

- Integration + E2E (requires compose stack running):

```bash
./scripts/integration_check.sh
# or:
make gate-integration
```

- Fault tests (requires compose stack running):

```bash
./scripts/fault_check.sh
# or:
make gate-fault
```

Coverage gate:

```bash
./scripts/coverage_gate.sh
# or:
make coverage
```

Docker lock fidelity checks:

```bash
make docker-lock
make docker-lock-runtime
```

Coverage policy:

- global floor: `75%`
- critical module floor: `80%` for `app`, `services/billing`, `worker_tasks`, `reaper`
- latest reports:
  - `worklog/baselines/coverage-latest.json`
  - `worklog/baselines/coverage-latest.xml`

Security lint note:

- Bandit rule `B104` is skipped via `pyproject.toml` because this compose deployment intentionally binds `0.0.0.0` inside containers (`uvicorn --host 0.0.0.0`).
- Network exposure remains controlled by Docker Compose port mapping and host firewall policy.

## Observability

- API metrics: `http://localhost:8000/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (default admin/admin)
- Worker metrics target: `worker:9100` (scraped by Prometheus)
- Prometheus alert rules: `monitoring/prometheus/alerts.yml` (loaded by `monitoring/prometheus/prometheus.yml`)
- Alertmanager is not included in this compose baseline; rules are validated at scrape/evaluation level.
