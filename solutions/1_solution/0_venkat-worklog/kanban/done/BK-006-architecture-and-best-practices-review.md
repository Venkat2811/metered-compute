# BK-006: Architecture and Best Practices Review

Priority: P1
Status: done
Depends on: P0-010

## Objective

Execute a formal architecture/code-quality review focused on async correctness, Redis/Streams usage, DB model/indexes, recovery/reaper behavior, logging/metrics quality, and deployment posture.

## Findings (severity-ranked)

### 1) High: Tracing posture does not match RFC/compose intent

- Evidence:
  - RFC and matrix describe OTel/Tempo for solution1 (`solutions/0_1_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`).
  - Compose includes `tempo` and `otel-collector` behind `tracing` profile (`solutions/1_solution/compose.yaml`).
  - Runtime code has no OpenTelemetry instrumentation usage (`solution1/` has no `opentelemetry` imports or tracer setup).
  - `tests/unit/test_observability_configs.py` only asserts config artifacts exist, not active export.
- Impact:
  - `docker compose --profile tracing up -d` does not give traceability for API/worker/reaper request flows; incident response relies on logs only.
- Remediation:
  - Implement explicit instrumentation in API, worker, and reaper (or change wording to explicit profile-only artifact mode).
  - Add smoke/integration check that emits a trace and verifies span export in profile mode.

### 2) Low: Secret/default-key handling already enforced by BK-004

- Evidence:
  - `BK-004` hardens startup for non-dev environments in `AppSettings` and rejects default placeholders/weak secrets.
  - `AppSettings` loads `.env.dev.defaults` only when `APP_ENV=dev`, and strict checks remain in production mode.
- Impact:
  - No additional code action required for this finding.
- Remediation:
  - Keep BK-004 guardrails in place and preserve these checks during config migrations.

### 3) Medium: Admin credit mutation lacks non-negative balance guard in DB path

- Evidence:
  - `users` table is created without `CHECK (credits >= 0)` in migrations (`src/solution1/db/migrations/0001_create_users_base.sql` and `0002...`).
  - `admin_update_user_credits` and transactional variant apply raw `credits = credits + $1` without a floor guard (`src/solution1/db/repository.py`).
- Impact:
  - Admin can persistently set negative credits via delta replay or mis-configuration; this is mirrored back into Redis via drift sync.
- Remediation:
  - Add DB-level non-negative constraints on `users.credits` and enforce delta floor checks.
  - Return deterministic conflict status for disallowed admin deltas.

### 4) Medium: Worker startup blocking is intentional simulation behavior

- Evidence:
  - `WorkerModel.__init__` performs `time.sleep(10)` (`src/solution1/workers/stream_worker.py`).
- Impact:
  - Startup is delayed by design for simulation behavior; because the worker is sync-process-based, this is not an async event-loop starvation issue.
- Remediation:
  - Revisit only if execution model changes to async streaming workers; otherwise monitor startup tails and restart behavior in deployment docs.

### 5) Medium: Reaper control-plane scan is unbounded and scan-order-sensitive at scale

- Evidence:
  - `_process_pending_markers` uses `redis_client.scan_iter(match="pending:*")` across the full keyspace each cycle (`src/solution1/workers/reaper.py`).
  - `_process_stuck_tasks` + `_run_credit_drift_audit` are periodic full scans of all running terminals/snapshots.
- Impact:
  - Recovery work can become heavy as keyspace/task volumes grow, competing with OLTP/worker activity.
- Remediation:
  - Bound scan workloads with cursor-aware pacing and/or partitioned keysets; batch snapshot/drift scans with limits and backoff.

### 6) Medium: Stream checkpoint persistence is written but never used for resume logic

- Evidence:
  - `STREAM_CHECKPOINT_UPDATES_TOTAL` and `upsert_stream_checkpoint` are implemented (`src/solution1/workers/stream_worker.py`), and `stream_checkpoints` migration exists.
  - Startup logic does not read or seed from `stream_checkpoints`.
- Impact:
  - Added persistence surface increases write load without improving recovery semantics.
- Remediation:
  - Either consume checkpoint state for bounded recovery windows (or remove this write-path and simplify migration/schema).

### 7) Low: Worker readiness visibility is indirect and depends on external probe conventions

- Evidence:
  - No container healthcheck for worker/reaper services in `compose.yaml`.
  - API readiness checks heartbeat key (`src/solution1/system_routes.py`, `src/solution1/app.py`), not service-level liveness/deadness of worker process.
- Impact:
  - Deployments may pass startup while worker is alive but non-functional in ways not reflected quickly in scheduling decisions.
- Remediation:
  - Add lightweight service healthcheck endpoints or a startup probe contract and container healthchecks for worker and reaper.

### 8) Low: Stream worker does not instrument metrics around all control-plane edge paths

- Evidence:
  - Metrics cover task completions and Lua latency, but not key edge branches (e.g., reaped orphans/claim-heavy paths) in stream worker and reaper.
  - Existing gauges/histograms focus on end outcome counters (`src/solution1/observability/metrics.py`).
- Impact:
  - Limited visibility into recovery pressure and reclaim churn.
- Remediation:
  - Add explicit counters for reclaimed/invalid messages and orphan-drop reasons.

## Review outcomes

- Scope covered per objective: async usage, Redis/Streams patterns, migration/index posture, recovery model, logging/metrics, and deployment posture.
- No code changes were made for this card.

## Recommended follow-ups

- BK-003: Implement/explicitly document OTel runtime behavior.
- BK-004: Preserve runtime secret-policy checks during future auth/config refactors.
- BK-002 or BK-008: Control-plane scan pacing and reclamation resource controls.

## Validation

- Static review only (`BK-006` scope).
- Confirmed file-by-file against solution docs (`RFC-0001*`), runtime settings/contracts, and implementation.
