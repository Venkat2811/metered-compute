# Solution 0 Runbook (Execution + TDD)

Last updated: 2026-02-15

This runbook drives implementation and verification for the pragmatic baseline:
FastAPI + Celery + Redis + Postgres under Docker Compose with strict type and test gates.

Path normalization (BK-016):

- container assets:
  - `api/` -> `docker/api/`
  - `worker/` -> `docker/worker/`
  - `reaper/` -> `docker/reaper/`
- observability assets:
  - `prometheus/` -> `monitoring/prometheus/`
  - `grafana/` -> `monitoring/grafana/`

## 0. Scope

References:

- `../../../.0_agentic_engineering/0_rfcs/RFC-0000-0-solution-celery-baseline/README.md`

## 1. Local Environment

```bash
cd solutions/0_solution
uv venv --python 3.12 .venv
source .venv/bin/activate
```

After `P0-001` lands:

```bash
uv sync
```

## 2. Quality Gates (Local Fast Loop)

```bash
cd solutions/0_solution
source .venv/bin/activate
./scripts/quality_gate.sh
./scripts/coverage_gate.sh
# or:
make quality
make coverage
```

## 3. Integration Loop (Compose)

```bash
cd solutions/0_solution
docker compose up --build -d
pytest -q tests/integration
pytest -q tests/e2e
pytest -q tests/fault
```

Teardown:

```bash
docker compose down -v
```

## 4. Migration Workflow

Apply migrations using settings DSN:

```bash
cd solutions/0_solution
source .venv/bin/activate
python -m solution0.db.migrate
```

Or override DSN explicitly:

```bash
python -m solution0.db.migrate --dsn postgresql://postgres:postgres@localhost:5432/postgres
```

## 5. Canonical API Smoke

Use seeded keys from `.env.dev.defaults`:

- admin: `${ADMIN_API_KEY}`
- user: `${ALICE_API_KEY}`

Example flow (target behavior):

1. Submit `POST /v1/task`
2. Poll `GET /v1/poll`
3. Optionally cancel `POST /v1/task/{id}/cancel`
4. Top-up via `POST /v1/admin/credits`

## 6. TDD Operating Contract

For each kanban card:

1. Add failing tests first (document exact failing command)
2. Implement minimal code to pass
3. Refactor with behavior unchanged
4. Record evidence under card progress notes

## 7. Required Test Matrix

- Unit:
  - Lua decision paths (`ok`, `cache_miss`, `insufficient`, `concurrency`, `idempotent`)
  - auth cache-aside behavior
  - request/response model validation
- Integration:
  - submit/poll success
  - credit deduction/refund
  - admin top-up
  - idempotency conflict handling
- E2E:
  - demo script success from clean compose start
- Fault:
  - worker crash -> eventual refund
  - Redis down -> graceful 503
  - Postgres down -> readiness fail / controlled degradation

## 8. Observability Minimum

Must be available in compose:

- structured JSON logs (`structlog`)
- `/metrics` exposure from API and worker
- Prometheus scraping
- Grafana dashboard provisioning

## 9. Baseline and Release Gate Artifacts

- Template: `baselines/TEMPLATE.md`
- Gate config:
  - `baselines/gates.unit.yaml`
  - `baselines/gates.integration.yaml`
  - `baselines/gates.release.yaml`

## 10. Reproducibility and Secrets Policy

Local/dev only defaults are allowed for deterministic demo runs.
Production policy remains:

- no hardcoded secrets
- external secret store
- key rotation and revocation

## 11. Pool Tuning and Saturation Notes

- Policy doc: `research/2026-02-15-pool-lifecycle-policy.md`
- Runtime defaults:
  - API pool: `min=1`, `max=10`
  - Worker/reaper pool max bounded to `<=8`
- Saturation signals:
  - submit `503` rate
  - DB wait events in `pg_stat_activity`
  - queue depth + request latency
- If submit path starts failing from pool pressure:
  1. verify admission rejection rates (`429`) are still high under bursts
  2. tune pool max conservatively
  3. re-run stress and lock review before further increase

## 12. Graceful Shutdown Drills

- Drill guide: `research/2026-02-15-shutdown-and-sigterm-drills.md`
- Core commands:
  - `docker compose stop worker`
  - `docker compose stop redis && docker compose start redis`
  - `docker compose stop postgres && docker compose start postgres`
- Validation after each drill:
  1. `GET /ready` reflects degraded/recovered state
  2. submit path returns controlled error contracts (`503` where expected)
  3. credits/task states converge via compensation/reaper logic
