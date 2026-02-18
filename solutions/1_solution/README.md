# 1_solution: Redis-Native Engine

Production implementation for the metered-compute API assignment using `JWT + Redis Streams + Lua atomic admission`.

Compose project name: `mc-solution1` (set in `compose.yaml`).

Primary references:

- `../0_1_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`
- `../README.md`
- `../../../original-task/api_playground-master/README.md`

Compatibility endpoints from the original assignment are still wired (`/task`, `/poll`, `/admin/credits`, `/hit`).

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
./scripts/wait_ready.sh
docker compose ps
```

3. See demo

```bash
source .venv/bin/activate
python ./utils/demo.py
# optional shell flow:
./utils/demo.sh
```

Optional full proof command (quality + coverage + clean compose rebuild + integration/e2e/fault + scenarios + log capture):

```bash
make prove
# alias:
make full-check
```

Artifacts are written to:

- `worklog/evidence/full-check-<timestamp>/`
- scenario report, compose logs, per-service logs, quality outputs, metrics snapshots

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

Seeded defaults are sourced from `.env.dev.defaults` and rendered into migrations/settings at runtime:

- `ADMIN_API_KEY`
- `ALICE_API_KEY`
- `BOB_API_KEY`
- OAuth dev client IDs/secrets/user mappings

`AppSettings` reads `.env.dev.defaults` only when `APP_ENV=dev` (explicit environment variables take precedence).

Load defaults into shell for manual flows:

```bash
set -a
source ./.env.dev.defaults
set +a
```

## Security and Secret Handling

- `APP_ENV` controls strict secret policy:
  - `dev`: default reproducible values from `.env.dev.defaults` are accepted.
  - non-dev: API keys must be valid UUIDs and cannot use the dev placeholders.
- OAuth client secrets are rejected in non-dev if they are weak/default placeholders or shorter than 24 characters.
- JWKS refresh behavior:
  - `HYDRA_JWKS_CACHE_TTL_SECONDS` controls cached JWKS TTL.
  - expired cache entries are refreshed automatically on decode when the cache window elapses.
  - token-key misses trigger one forced-refresh attempt before rejecting a token.

Key management commands:

- Validate env contract in local non-dev container style:

```bash
# non-dev policy checks
source ./.env.dev.defaults  # optional for local reproducibility only
export APP_ENV=production
export ADMIN_API_KEY=<uuid>
export ALICE_API_KEY=<uuid>
export BOB_API_KEY=<uuid>
export OAUTH_ADMIN_CLIENT_SECRET=<min-24-chars>
export OAUTH_USER1_CLIENT_SECRET=<min-24-chars>
export OAUTH_USER2_CLIENT_SECRET=<min-24-chars>
```

- Secret manager/file-backed values are supported via `_FILE` variables:
  - `ADMIN_API_KEY_FILE`, `ALICE_API_KEY_FILE`, `BOB_API_KEY_FILE`
  - `OAUTH_ADMIN_CLIENT_SECRET_FILE`, `OAUTH_USER1_CLIENT_SECRET_FILE`, `OAUTH_USER2_CLIENT_SECRET_FILE`

## Reaper retention controls

- Reaper performs bounded cleanup for non-essential historical credit data each cycle:
  - `credit_transactions`
  - `credit_drift_audit`
- Env vars:
  - `REAPER_RETENTION_BATCH_SIZE` (default: `500`)
  - `REAPER_CREDIT_TRANSACTION_RETENTION_SECONDS` (default: `86400`)
  - `REAPER_CREDIT_DRIFT_AUDIT_RETENTION_SECONDS` (default: `86400`)
- Set either retention window to `0` to disable that table’s purge.
- Metrics:
  - `reaper_retention_deletes_total{table="credit_transactions"|"credit_drift_audit"}`

- Hydra admin endpoint logs may emit:
  `Value is sensitive and has been redacted...`
  when running local compose profiles. This is expected from Hydra’s
  request-logging policy. Treat it as non-blocking documentation-level noise unless
  explicit query-level debugging is required for local investigation.

## Demo Flows

### End-to-end submit/poll

```bash
./utils/demo.sh
```

Python demo script:

```bash
source .venv/bin/activate
python ./utils/demo.py
```

### Admin top-up (JWT path)

```bash
ADMIN_TOKEN=$(curl -sS -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${ADMIN_API_KEY}\",\"scope\":\"task:submit task:poll task:cancel admin:credits\"}" \
  | python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -sS -X POST http://localhost:8000/v1/admin/credits \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${ALICE_API_KEY}\",\"delta\":100,\"reason\":\"manual_adjust\"}"
```

### Revoke current JWT (durable blacklist)

```bash
USER_TOKEN=$(curl -sS -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${ALICE_API_KEY}\"}" \
  | python -c 'import json,sys;print(json.load(sys.stdin)[\"access_token\"])')

curl -sS -X POST http://localhost:8000/v1/auth/revoke \
  -H "Authorization: Bearer ${USER_TOKEN}"
```

Revocation durability model:

- Postgres partitioned table is the source of truth.
- Redis day buckets are hot cache (best-effort write).
- Auth check stays Redis-first, with Postgres fallback if Redis is unavailable.

### Scenario harness

```bash
source .venv/bin/activate
python ./scripts/run_scenarios.py
```

This exercises:

- JWT auth, admin top-up, submit/poll via `/v1/*` and compatibility `/task` + `/poll`
- idempotency replay and conflict paths
- insufficient-credit behavior
- cancel while worker is paused
- multi-user concurrency and tier behavior
- demo script execution and tier/model stress

### Webhook callbacks (optional)

Register/update callback URL:

```bash
USER_TOKEN=$(curl -sS -X POST http://localhost:8000/v1/oauth/token \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${ALICE_API_KEY}\"}" \
  | python -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')

curl -sS -X PUT http://localhost:8000/v1/webhook \
  -H "Authorization: Bearer ${USER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"callback_url":"https://webhook.site/<your-endpoint>","enabled":true}'
```

Replay dead-letter items back to pending queue:

```bash
source .venv/bin/activate
python ./scripts/replay_webhook_dlq.py --limit 50
```

### Stream profiling and capacity compare

```bash
source .venv/bin/activate
python ./scripts/load_harness.py \
  --output worklog/evidence/load/latest-load-report.json

python ./scripts/capacity_model.py \
  --input worklog/evidence/load/latest-load-report.json \
  --output-markdown worklog/evidence/load/latest-capacity-model.md \
  --output-json worklog/evidence/load/latest-capacity-model.json
```

For baseline-vs-tuned comparison:

```bash
python ./scripts/capacity_model.py \
  --input worklog/evidence/load/tuned.json \
  --compare-input worklog/evidence/load/baseline.json \
  --output-markdown worklog/evidence/load/compare.md \
  --output-json worklog/evidence/load/compare.json
```

### Tracing profile (optional)

Bring up tracing runtime (Tempo + OTel collector + app exporters enabled):

```bash
OTEL_ENABLED=true docker compose --profile tracing up --build -d
./scripts/wait_ready.sh
python ./utils/demo.py
```

Inspect traces in Grafana Explore (`http://localhost:3000`, default `admin/admin`) with TraceQL:

```text
{resource.service.name="mc-solution1-api"}
{name="stream_worker.process_message"}
{name="reaper.cycle"}
```

Expected shape:

- API submit spans from `solution1.api`
- worker consume/process spans from `solution1.worker` linked via propagated `trace_context`
- reconciler cycle spans from `solution1.reaper`

## Stack

- API: FastAPI (`src/solution1/main.py`)
- Admission gate: Redis Lua mega-script (`src/solution1/utils/lua_scripts.py`)
- Execution: Redis Streams consumer-group worker (`src/solution1/workers/stream_worker.py`)
- Storage: Postgres (system-of-record) + Redis (hot-path/auth/credits/task state)
- Reconciliation: reaper (`python -m solution1.workers.reaper`)
- Webhook delivery: dispatcher (`python -m solution1.workers.webhook_dispatcher`)
- Auth: Hydra-issued JWT + local verification in API
- Observability: Prometheus + Grafana + structured JSON logs
- Optional tracing profile: Tempo + OTel collector (`docker compose --profile tracing up -d`)
- Deployment target for this solution: Docker Compose (`compose.yaml`)

## DB-call profile (what is actually reduced)

For a typical lifecycle (`submit -> poll xN -> complete`):

- Auth path: `0` PG calls on healthy Redis (JWT verify is local crypto; revocation check is Redis)
- Submit path: `1` PG transaction (task row + audit write after Redis Lua admission)
- Poll path: `0` PG calls on hot path (`result:{task_id}` / `task:{task_id}` Redis first)
- Worker path: `3` PG writes (guarded `RUNNING`, terminal transition, checkpoint upsert)
- Revocation write path: `1` PG insert (`POST /v1/auth/revoke`) with Redis cache write best-effort

Net effect: this design removes repeat poll SELECT load from Postgres while keeping PG as source-of-record for durable task/audit state.

## Lay Of The Land (Code Structure)

Generated with:

```bash
LC_ALL=C tree -a -L 2 -I '__pycache__|*.pyc|.pytest_cache|.mypy_cache|.ruff_cache|.venv|postgres_data|evidence|.coverage|coverage.xml|src/mc_solution1.egg-info'
LC_ALL=C tree -a -L 2 -I '__pycache__|*.pyc' src/solution1
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
|   |-- hydra
|   |-- postgres
|   |-- reaper
|   `-- worker
|-- monitoring
|   |-- grafana
|   |-- otel
|   |-- prometheus
|   `-- tempo
|-- scripts
|-- src
|   `-- solution1
|-- tests
|   |-- e2e
|   |-- fault
|   |-- integration
|   `-- unit
`-- utils
```

Source package shape:

```text
src/solution1
|-- api
|   |-- admin_routes.py
|   |-- contracts.py
|   |-- error_responses.py
|   |-- paths.py
|   |-- system_routes.py
|   |-- task_read_routes.py
|   |-- task_write_routes.py
|   `-- webhook_routes.py
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
|   |-- metrics.py
|   `-- tracing.py
|-- services
|   |-- auth.py
|   |-- billing.py
|   `-- webhooks.py
|-- utils
|   |-- logging.py
|   |-- lua_scripts.py
|   `-- retry.py
`-- workers
    |-- reaper.py
    |-- stream_worker.py
    `-- webhook_dispatcher.py
```
