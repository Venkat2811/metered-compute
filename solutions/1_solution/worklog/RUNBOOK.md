# Solution 1 Runbook (Execution + TDD)

Last updated: 2026-02-16

This runbook drives implementation and verification for the Redis-native track:
FastAPI + OAuth service + Redis Streams + Postgres control plane under Docker Compose,
with strict typing and test gates.

## 0. Scope

References:

- `../../../.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`
- `../../README.md` (matrix + code/RFC boundary)

## 1. Local Environment

```bash
cd solutions/1_solution
uv venv --python 3.12 .venv
source .venv/bin/activate
uv sync --frozen
```

## 2. Quality Gates (Fast Loop)

```bash
cd solutions/1_solution
source .venv/bin/activate
make fmt
make lint
make typecheck
make test-unit
```

One-shot local proof:

```bash
make prove
```

## 3. Integration Loop (Compose)

```bash
cd solutions/1_solution
docker compose up --build -d
pytest -q tests/integration
pytest -q tests/e2e
pytest -q tests/fault
```

Teardown:

```bash
docker compose down -v --remove-orphans
```

## 4. Migration Workflow

Apply migrations using settings DSN:

```bash
cd solutions/1_solution
source .venv/bin/activate
python -m solution1.db.migrate
```

## 5. Canonical API Smoke

Target flow:

1. `POST /v1/oauth/token` with seeded API key
2. `POST /v1/task` with `Bearer <jwt>` + `Idempotency-Key`
3. `GET /v1/poll?task_id=<id>` until terminal
4. `POST /v1/task/{id}/cancel` (non-terminal path)
5. `POST /v1/admin/credits` with admin JWT

## 6. TDD Operating Contract

For each kanban card:

1. Add failing tests first and record the failing command
2. Implement minimal changes to make tests pass
3. Refactor with behavior unchanged
4. Record command evidence in the card notes

## 7. Required Test Matrix

- Unit:
  - JWT issuance/validation/revocation checks
  - Lua mega-script decision paths (`ok`, `cache_miss`, `insufficient`, `concurrency`, `idempotent`)
  - route contract validation and error taxonomy
  - worker state transitions and PEL recovery logic
- Integration:
  - submit/poll/cancel/admin through compose stack
  - per-user idempotency scope and concurrency limits
  - zero-Postgres hot path for submit/poll happy path
- E2E:
  - demo script success from clean compose start
- Fault:
  - worker crash -> PEL reclaim and eventual terminal state
  - Redis restart -> controlled 503 and recovery
  - Postgres outage -> hot path continues, reconciler degrades safely

## 8. Observability Minimum

Must be available in compose baseline:

- structured JSON logs (`structlog`)
- `/metrics` exposure from API, worker, reconciler, oauth service
- Prometheus scraping + Grafana provisioning

Config-included RFC scope:

- Alertmanager rules
- OTel collector + Tempo tracing config

## 9. Baseline and Release Gate Artifacts

- Template: `baselines/TEMPLATE.md`
- Gate config:
  - `baselines/gates.unit.yaml`
  - `baselines/gates.integration.yaml`
  - `baselines/gates.release.yaml`

## 10. Reproducibility and Secrets Policy

Local/dev deterministic defaults are allowed for reproducible demos.
Production policy remains:

- no hardcoded secrets
- keys and credentials from external secret manager
- key rotation and revocation support

### Non-dev hardening reminders

- API keys (`ADMIN_API_KEY`, `ALICE_API_KEY`, `BOB_API_KEY`) must be UUIDs.
- OAuth client secrets must not use default local placeholder values and must be at least 24 characters.
- Secret values may be sourced from `_FILE` env vars (e.g., `ADMIN_API_KEY_FILE`) and must resolve to non-placeholder values.
- Token validation requires a valid `jti` claim and refreshes JWKS on cache miss/rotation path.

### JWKS cache and rotation checks

- JWKS cache TTL is controlled by `HYDRA_JWKS_CACHE_TTL_SECONDS`.
- Key-rotation validation commands:
  1. Issue a token before rotation (`CLIENT CREDENTIALS` path).
  2. Rotate signing key in Hydra.
  3. Retry a short request and confirm no unauthorized regression.
  4. Confirm JWKS path recovers from key-miss with one forced refresh attempt.

## 11. BK-008 Credit-Refund Durability Register

- Canonical register: `../../../.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/data-ownership.md#credit-refund-durability-risk-register-solution1-scope`.
- BK-008 is tracked as a runtime risk register and production guardrail baseline.
- Operational checks:
  - API log scan (5 min window):
    - `docker compose logs api --tail 400 | rg "task_persist_compensation_failed|stream_task_failure_db_update_failed"`
  - Reaper log scan (5 min window):
    - `docker compose logs reaper --tail 400 | rg "reaper_stuck_task_refund_error|reaper_cycle|task_persist_compensation_failed"`
  - Prometheus ratio checks (example):
    - `sum(rate(task_submissions_total{result="persist_failure"}[5m])) / sum(rate(task_submissions_total[5m])) > 0.1`
    - `sum(rate(reaper_refunds_total{reason="stuck_task"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`
    - `sum(rate(reaper_refunds_total{reason="orphan_marker"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`
- Trigger threshold to promote BK-008 to active remediation work:
  - any one condition above sustained for 30 minutes, or equivalent absolute spike in the same signals.

## 12. Streams and Recovery Notes

- Stream: `tasks:stream`, consumer group: `workers`
- PEL recovery policy: claim entries idle beyond configured threshold
- Reconciler responsibilities:
  - dirty credit snapshot flush to Postgres
  - drift audit between Redis balances and Postgres snapshots
  - task expiry cleanup

### Retention enforcement (BK-011)

- Reaper retention controls are configured via:
  - `REAPER_RETENTION_BATCH_SIZE`
  - `REAPER_CREDIT_TRANSACTION_RETENTION_SECONDS`
  - `REAPER_CREDIT_DRIFT_AUDIT_RETENTION_SECONDS`
- Purge operation is bounded (`REAPER_RETENTION_BATCH_SIZE`) and runs inside each `reaper` cycle.
- Safe rollback check:
  - `docker compose logs reaper --since=10m | rg "reaper_cycle"`
  - Confirm `reaper_retention_deletes_total` counters move only on non-zero old data windows.
- Alerting suggestion (optional):
  - keep an eye on `reaper_retention_deletes_total` slope changes at restart or after large compaction bursts.
