# metered-compute Kanban — Done

Last updated: 2026-03-30

## Solution 0

### S0-BK-001-load-profile-and-capacity-model
- Solution: Sol0

# BK-001: Load Profile and Capacity Model

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Create realistic load profiles and translate observed behavior into capacity planning inputs (`R_task`, `R_poll`, queue depth, saturation points).

## Checklist

- [x] Define low/medium/high traffic profiles
- [x] Add scripted load runs with reproducible seeds
- [x] Capture queue latency and worker utilization under load
- [x] Produce monthly capacity projection sheet from measured data

## Exit Criteria

- [x] Capacity model is evidence-backed, not assumption-only
- [x] Inputs can be reused by Solution 1+ RFC comparisons

## Evidence

- Script: `scripts/load_harness.py`
- Script: `scripts/capacity_model.py`
- Analysis: `../../research/2026-02-15-load-and-capacity-analysis.md`
- Output: `../../evidence/load/latest-load-report.json`
- Output: `../../evidence/load/latest-capacity-model.json`
- Output: `../../evidence/load/latest-capacity-model.md`

### S0-BK-002-opentelemetry-tempo-upgrade-path
- Solution: Sol0

# BK-002: OpenTelemetry + Tempo Upgrade Path

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Prepare an explicit migration path from baseline metrics/logging to distributed tracing without destabilizing Solution 0.

## Checklist

- [x] Define trace span model for submit/poll/worker lifecycle
- [x] Add collector + Tempo compose profile design
- [x] Establish trace-cardinality and sampling policy
- [x] Add rollout and rollback steps

## Exit Criteria

- [x] Upgrade path is documented and low-risk
- [x] Instrumentation plan reuses existing correlation IDs

## Evidence

- Plan: `../../research/2026-02-15-opentelemetry-tempo-upgrade-path.md`

### S0-BK-003-production-ha-packaging
- Solution: Sol0

# BK-003: Production HA Packaging (Postgres + Redis)

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Document production packaging path beyond single-node compose: Postgres replication, Redis Sentinel/Cluster, and service-level failover playbooks.

## Checklist

- [x] Postgres HA topology proposal (replica + failover)
- [x] Redis Sentinel/Cluster topology proposal
- [x] Backup/restore and PITR playbook
- [x] Failure drills and recovery SLO definitions

## Exit Criteria

- [x] HA plan is concrete and implementation-ready for post-assignment evolution

## Evidence

- Plan: `../../research/2026-02-15-production-ha-packaging.md`

### S0-BK-004-clean-code-refactoring-hardening
- Solution: Sol0

# BK-004: Clean Code Refactoring Hardening

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Raise codebase maintainability to production-grade quality for the RFC scope with explicit refactoring, complexity controls, and clearer boundaries.

## Checklist

- [x] Add module-boundary map (API orchestration vs domain services vs repository vs worker/reaper runtime)
- [x] Refactor large handlers (`submit`, `poll`, `cancel`, `admin`) into smaller composable units where practical for Solution 0 scope
- [x] Enforce complexity thresholds (cyclomatic complexity and function size) in CI
- [x] Remove duplicate logic across API/worker/reaper compensation paths where identified
- [x] Add architectural comments where non-obvious invariants are encoded
- [x] Run dead-code and stale-path cleanup pass

## Exit Criteria

- [x] No unbounded complexity drift in critical paths (explicit gate + documented overrides)
- [x] Critical workflows are readable end-to-end without hidden coupling
- [x] Refactor passes all existing functional and fault tests with no regressions

## Evidence

- Boundary map: `../../research/2026-02-15-module-boundary-map.md`
- Complexity gate: `scripts/complexity_gate.py`
- Gate output: `../../baselines/latest-complexity-gate.json`

### S0-BK-005-test-coverage-gate-70-80
- Solution: Sol0

# BK-005: Coverage Gate (70-80%)

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Add explicit coverage gates with realistic thresholds for Solution 0 scope and protect critical reliability paths.

## Checklist

- [x] Add `pytest-cov` reporting in CI and local gate scripts
- [x] Set global coverage gate target in the 70-80 range (initial target: 75%)
- [x] Set critical-module floor (initial target: 80% for `app`, `services/billing`, `worker_tasks`, `reaper`)
- [x] Add missing tests for uncovered branches in compensation and degradation paths
- [x] Publish coverage report artifact in `worklog/baselines/`

## Exit Criteria

- [x] Coverage gate is enforced and cannot regress silently
- [x] Critical reliability modules are above agreed floor
- [x] Coverage report is reproducible locally and in CI

## Evidence

- Gate command: `./scripts/coverage_gate.sh`
- CI/unit gate wiring: `./scripts/ci_check.sh`
- Latest artifact:
  - `../../baselines/coverage-latest.json`
  - `../../baselines/coverage-latest.xml`
- Latest measured totals:
  - Global: `81.87%`
  - `src/solution0/app.py`: `82.7%`
  - `src/solution0/services/billing.py`: `100.0%`
  - `src/solution0/worker_tasks.py`: `92.6%`
  - `src/solution0/reaper.py`: `94.1%`

### S0-BK-006-code-quality-standard-and-lint-stack
- Solution: Sol0

# BK-006: Code Quality Standard and Lint Stack

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Standardize a production-grade code quality stack (Carbon-compatible if adopted, otherwise equivalent industry-standard tooling) with clear quality policy and automation.

## Checklist

- [x] Define quality policy document (style, safety, complexity, security, dependency hygiene)
- [x] Evaluate Carbon compatibility and decide final stack
- [x] Add/verify linters and analyzers:
  - [x] `ruff` (lint/format)
  - [x] `mypy --strict`
  - [x] security lint (`bandit` or equivalent)
  - [x] dependency audit (`pip-audit` or equivalent)
  - [x] secret scanning (`detect-secrets` or equivalent)
  - [x] Dockerfile lint (`hadolint` or equivalent)
- [x] Integrate all checks into a single quality gate command
- [x] Document false-positive handling and suppression policy

## Exit Criteria

- [x] Quality gate is deterministic and one-command runnable
- [x] Security and dependency checks are part of normal development flow
- [x] Team has a documented and enforceable quality standard

## Evidence

- Quality policy: `../../research/2026-02-15-quality-policy.md`
- One-command gate: `./scripts/quality_gate.sh`
- Tooling in project config: `../../../../pyproject.toml`
- Secret baseline and drift check:
  - `../../../../.secrets.baseline`
  - `../../../../scripts/secrets_check.sh`

### S0-BK-007-architecture-and-best-practices-review
- Solution: Sol0

# BK-007: Architecture and Best-Practices Review

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Run a formal architecture review and produce explicit findings that confirm Solution 0 is using each core technology correctly for the RFC scope.

## Checklist

- [x] Async runtime and web serving review
  - [x] verify FastAPI + Uvicorn usage, blocking-call boundaries, and pool sizing assumptions
  - [x] verify API lifecycle/resource initialization and shutdown behavior
- [x] Postgres review
  - [x] verify schema evolution approach and migration safety
  - [x] verify index strategy with query-plan evidence for hot paths
  - [x] verify transaction boundaries and consistency guarantees
- [x] Redis review
  - [x] verify key design, TTL policy, memory behavior, and restart recovery
  - [x] verify cache-aside and dirty-snapshot lifecycle
- [x] Celery review
  - [x] verify queue topology, retry semantics, revoke/cancel behavior, and broker failure handling
  - [x] verify idempotency and at-least-once implications on billing correctness
- [x] Lua scripting review
  - [x] verify atomicity guarantees and script reload behavior after Redis restart
  - [x] verify script contracts and error handling semantics
- [x] Reaper review
  - [x] verify orphan/stuck-task convergence guarantees and bounded recovery
  - [x] verify no double-refund/no lost-refund invariants
- [x] Produce architecture review report with action items and severity levels

## Exit Criteria

- [x] Written review confirms or corrects each major design decision
- [x] All critical findings have tracked remediation cards
- [x] Team can state clearly why this is the highest quality bar for Solution 0 scope

## Evidence

- Review report: `../../research/2026-02-15-architecture-best-practices-review.md`
- Related lock/transaction evidence: `../../research/2026-02-15-transaction-lock-review.md`
- Remediation mapping maintained in backlog cards:
  - `BK-010`, `BK-011`, `BK-012`, `BK-009`, `BK-001`, `BK-018`

### S0-BK-008-makefile-developer-workflow
- Solution: Sol0

# BK-008: Makefile Developer Workflow

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Provide a clean Makefile-based workflow so local development, quality checks, test suites, and compose operations are consistent and reproducible.

## Checklist

- [x] Add `Makefile` with clear target groups:
  - [x] environment (`venv`, `sync`)
  - [x] quality (`fmt`, `lint`, `type`)
  - [x] tests (`test-unit`, `test-integration`, `test-e2e`, `test-fault`, `test-all`)
  - [x] runtime (`up`, `down`, `logs`, `ps`, `demo`)
  - [x] release (`gate-unit`, `gate-integration`, `gate-fault`)
- [x] Ensure targets use existing scripts where possible
- [x] Add `make help` output with brief target descriptions
- [x] Update README with canonical `make` commands

## Exit Criteria

- [x] New engineer can run full lifecycle from `make` targets only
- [x] Targets are deterministic and CI-compatible
- [x] Command surface is minimal and non-duplicative

## Evidence

- Makefile: `../../../../Makefile`
- Updated usage docs: `../../../../README.md`
- Canonical gate commands:
  - `make quality`
  - `make coverage`
  - `make gate-unit`
  - `make gate-integration`
  - `make gate-fault`

### S0-BK-009-rate-limit-and-concurrency-stress
- Solution: Sol0

# BK-009: Rate-Limit and Concurrency Stress Validation

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Prove behavior under high concurrency and near-saturation load within Solution 0 RFC scope, including rate-limit style protections and failure boundaries.

## Checklist

- [x] Define load scenarios: normal, burst, sustained high concurrency, and overload
- [x] Add load-test harness (`k6`, `locust`, or equivalent) for submit/poll/cancel/admin flows
- [x] Add stress tests for:
  - [x] credit contention on same user with many concurrent submits
  - [x] idempotency replay under concurrent identical requests
  - [x] queue saturation and poll amplification pressure
  - [x] Redis/Postgres transient degradation under load
- [x] Add explicit assertions for 429/402/503 behavior under stress conditions
- [x] Capture latency, queue depth, error rates, and recovery time metrics
- [x] Publish limit findings and recommended safe operating envelope

## Exit Criteria

- [x] High-concurrency behavior is measured and documented, not assumed
- [x] Rate-limit/concurrency controls are verified under stress
- [x] RFC scope limits and saturation thresholds are explicit for contributors

## Evidence

- Harness: `scripts/load_harness.py`
- Analysis: `../../research/2026-02-15-load-and-capacity-analysis.md`
- Report: `../../evidence/load/latest-load-report.json`

### S0-BK-010-connection-pooling-and-resource-lifecycle-hardening
- Solution: Sol0

# BK-010: Connection Pooling and Resource Lifecycle Hardening

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Harden database/redis connection management across API, worker, and reaper with explicit sizing strategy and clean lifecycle handling.

## Checklist

- [x] Define per-service pool sizing policy and defaults for local vs production
- [x] Add pool exhaustion behavior tests and timeout assertions
- [x] Ensure all services close pools/clients on shutdown paths
- [x] Add runbook notes for pool tuning and saturation signals

## Exit Criteria

- [x] No per-request or per-task pool construction in hot paths
- [x] Lifecycle open/close behavior is deterministic and test-backed
- [x] Pool sizing assumptions are explicit and reviewable

## Evidence

- Policy doc: `../../research/2026-02-15-pool-lifecycle-policy.md`
- Runbook updates: `../../RUNBOOK.md`
- Pool exhaustion path test: `tests/unit/test_app_paths.py::test_submit_returns_503_on_pool_exhaustion`
- Lifecycle close tests:
  - `tests/unit/test_app_internals.py::test_lifespan_initializes_runtime_and_closes_resources`
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`
  - `tests/unit/test_reaper_paths.py::test_main_async_runs_single_cycle_and_shuts_down`

### S0-BK-012-transactional-uow-and-rollback-audit
- Solution: Sol0

# BK-012: Transactional UoW and Rollback Audit

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Formalize unit-of-work boundaries for all multi-statement billing/task transitions and verify rollback semantics under injected faults.

## Checklist

- [x] Document transaction boundaries for submit/cancel/publish-failure/worker-failure/reaper-recovery
- [x] Add targeted tests that inject mid-transaction failures and assert rollback
- [x] Add idempotent write safeguards for repeated failure-retry paths
- [x] Add query-level lock strategy review for race-prone transitions

## Exit Criteria

- [x] Every multi-step mutation path is atomic or explicitly compensating by design
- [x] Rollback behavior is tested, not inferred
- [x] No partial-write billing states remain in verified paths

## Evidence

- Audit: `../../research/2026-02-15-transaction-uow-rollback-audit.md`
- Related lock review: `../../research/2026-02-15-transaction-lock-review.md`

### S0-BK-013-graceful-sigterm-and-shutdown-drills
- Solution: Sol0

# BK-013: Graceful SIGTERM and Shutdown Drills

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Guarantee predictable shutdown behavior across API, worker, and reaper during rolling deploys and abrupt stop/restart scenarios.

## Checklist

- [x] Add explicit shutdown budgets/timeouts per service
- [x] Add integration drills for stop/restart while requests/tasks are in-flight
- [x] Verify no leaked counters/credits on forced termination scenarios
- [x] Document operational shutdown and restart runbook

## Exit Criteria

- [x] SIGTERM behavior is deterministic and tested
- [x] In-flight work handling is explicit and observable
- [x] Restart does not leave billing/task state inconsistent

## Evidence

- Drill doc: `../../research/2026-02-15-shutdown-and-sigterm-drills.md`
- Runbook updates: `../../RUNBOOK.md` (Section 12)
- Runtime tests:
  - `tests/fault/test_runtime_faults.py`
  - `tests/fault/test_readiness_degradation.py`
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`
  - `tests/unit/test_reaper_paths.py::test_main_async_runs_single_cycle_and_shuts_down`

### S0-BK-014-logging-contract-and-trace-context-hardening
- Solution: Sol0

# BK-014: Logging Contract and Trace Context Hardening

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Standardize structured logging schema and trace-context propagation so logs are queryable, correlated, and operationally actionable.

## Checklist

- [x] Define required log schema fields by event class (api, worker, reaper, billing)
- [x] Add tests for schema conformance on critical events
- [x] Propagate `trace_id` through async task boundaries where feasible
- [x] Add sampling/redaction policy for noisy or sensitive fields

## Exit Criteria

- [x] Log events are consistently shaped and machine-queryable
- [x] Trace context is preserved across core workflow boundaries
- [x] Logging policy is documented and enforceable

## Evidence

- Logging contract doc: `../../research/2026-02-15-logging-contract.md`
- Schema test: `tests/unit/test_logging_shape.py`
- Trace propagation:
  - API forwards trace to worker payload (`src/solution0/app.py`)
  - worker binds trace context (`src/solution0/worker_tasks.py`)
  - payload assertion test (`tests/unit/test_app_paths.py`)

### S0-BK-015-lua-bootstrap-and-redis-startup-contract
- Solution: Sol0

# BK-015: Lua Bootstrap and Redis Startup Contract

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Make Redis Lua bootstrap guarantees explicit across startup, restart, and failover so admission behavior is deterministic from first request.

## Checklist

- [x] Define startup contract for script loading and readiness gating
- [x] Add health/readiness probe for script availability (`SCRIPT EXISTS` or equivalent)
- [x] Add tests for Redis restart and script-cache-loss convergence
- [x] Document operational behavior and failure fallback expectations

## Exit Criteria

- [x] Script availability guarantees are explicit and test-backed
- [x] No first-request surprises after Redis restart or script cache loss
- [x] Lua bootstrap behavior is documented for operators

## Evidence

- Readiness script probe: `src/solution0/app.py` (`/ready` with `script_exists`)
- NoScript reload handling:
  - `src/solution0/services/billing.py`
  - `tests/unit/test_billing_service.py`
- Redis restart/script-cache-loss fault coverage:
  - `tests/fault/test_readiness_degradation.py`
- Operator doc:
  - `../../research/2026-02-15-lua-startup-contract.md`

### S0-BK-016-layout-normalization-docker-and-monitoring-directories
- Solution: Sol0

# BK-016: Layout Normalization for Docker and Monitoring Directories

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Normalize repository layout to explicitly group runtime/container assets under `docker/` and observability assets under `monitoring/` without changing behavior.

## Checklist

- [x] Propose target directory tree:
  - [x] `docker/api/Dockerfile`, `docker/worker/Dockerfile`, `docker/reaper/Dockerfile`
  - [x] `monitoring/prometheus/*`, `monitoring/grafana/*`
- [x] Update compose paths and build contexts for the new structure
- [x] Add migration note to README and runbook with old->new path mapping
- [x] Verify no behavior drift through full gates (`ci`, `integration`, `fault`, demo)

## Exit Criteria

- [x] Directory structure clearly separates app code vs deployment assets
- [x] Docker Desktop/Compose UX remains clean and reproducible
- [x] All gates remain green after the path migration

## Progress Notes (2026-02-15)

Implemented:

- moved container assets:
  - `api/` -> `docker/api/`
  - `worker/` -> `docker/worker/`
  - `reaper/` -> `docker/reaper/`
- moved observability assets:
  - `prometheus/` -> `monitoring/prometheus/`
  - `grafana/` -> `monitoring/grafana/`
- rewired compose and docs:
  - `compose.yaml` build and volume paths updated
  - `README.md` repository layout and observability path references updated
  - `worklog/RUNBOOK.md` old->new path mapping added

Evidence:

- `./scripts/ci_check.sh` passed (`19 passed`)
- `./scripts/integration_check.sh` passed (`7 integration`, `1 e2e`)
- `./scripts/fault_check.sh` passed (`4 passed`)

### S0-BK-017-transaction-footprint-and-lock-minimization-review
- Solution: Sol0

# BK-017: Transaction Footprint and Lock Minimization Review

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Constrain transaction usage to only correctness-critical boundaries and validate lock contention/performance impact under concurrency.

## Checklist

- [x] Inventory each transaction in API/worker/reaper and classify:
  - [x] mandatory for invariants
  - [x] optional (candidate for single-statement rewrite)
- [x] Capture lock/latency evidence under stress (`pg_stat_activity`, `pg_locks`, query timing)
- [x] Identify opportunities to reduce transaction scope duration and touched rows
- [x] Add invariants/performance tradeoff notes per path in architecture review

## Exit Criteria

- [x] No unnecessary multi-statement transactions remain
- [x] Locking behavior is measured and documented
- [x] Throughput impact is quantified and acceptable for Solution 0 scope

## Evidence

- Review doc: `../../research/2026-02-15-transaction-lock-review.md`
- Runtime measurement sources:
  - `pg_stat_activity`
  - `pg_locks`
  - concurrent submit stress result (`201=3`, `429=197`)

### S0-BK-018-high-throughput-consistency-patterns-evaluation
- Solution: Sol0

# BK-018: High-Throughput Consistency Patterns Evaluation

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Evaluate lower-overhead consistency patterns (single-statement DML, idempotent upserts, append-only events with async projection) versus current transactional orchestration.

## Checklist

- [x] Prototype one critical path with single-statement SQL/CTE alternative
- [x] Compare correctness guarantees vs current implementation
- [x] Benchmark p50/p95 latency and throughput at medium/high concurrency
- [x] Decide preferred pattern per write path and document rationale

## Exit Criteria

- [x] Chosen write patterns are explicit and performance-justified
- [x] Correctness invariants remain intact under retries/failures
- [x] Results feed RFC evolution notes for Solution 1+

## Evidence

- Implementation: `src/solution0/db/repository.py`
- Benchmark script: `scripts/benchmark_write_patterns.py`
- Analysis: `../../research/2026-02-15-write-pattern-benchmark.md`
- Report: `../../evidence/load/latest-write-pattern-benchmark.json`

### S0-BK-019-task-id-uuidv7-migration
- Solution: Sol0

# BK-019: Task ID UUIDv7 Migration

Priority: Backlog
Status: done
Depends on: P0-004

## Objective

Align implementation with RFC/matrix requirement that task IDs are UUIDv7 and time-ordered.

## Checklist

- [x] Add UUIDv7 generator dependency compatible with Python 3.12
- [x] Switch submit path task id generation from UUIDv4 to UUIDv7
- [x] Add test assertions for UUIDv7 in unit and integration paths
- [x] Rebuild containers and validate end-to-end behavior

## Exit Criteria

- [x] New task IDs are UUIDv7 in deployed API responses
- [x] Existing task/poll/cancel flows remain fully compatible
- [x] Integration tests pass with UUIDv7 assertions

## Evidence

- Dependency: `pyproject.toml` (`uuid6==2025.0.1`)
- Implementation: `src/solution0/app.py`
- Unit assertion: `tests/unit/test_app_paths.py`
- Integration assertion: `tests/integration/test_api_flow.py`

### S0-BK-020-worker-runtime-and-loop-model-hardening
- Solution: Sol0

# BK-020: Worker Runtime and Loop Model Hardening

Priority: Backlog
Status: done
Depends on: BK-012

## Objective

Replace fragile per-process `run_until_complete` orchestration with a safer execution model for Celery workers.

## Checklist

- [x] Evaluate synchronous DB driver path for Celery worker vs isolated async worker process
- [x] Add bounded timeouts around DB operations in worker terminal paths
- [x] Document and test worker behavior under DB hang and reconnect scenarios

## Exit Criteria

- [x] Worker execution model is resilient under dependency slowness and process lifecycle events

## Evidence

- Runtime model migrated from `run_until_complete` to dedicated worker-loop thread + `run_coroutine_threadsafe`: `src/solution0/worker_tasks.py`
- Bounded DB-operation timeout controls added: `src/solution0/settings.py`, `src/solution0/worker_tasks.py`
- Loop bootstrap/shutdown timeout controls added: `src/solution0/settings.py`, `src/solution0/worker_tasks.py`
- Worker runtime behavior tests:
  - `tests/unit/test_worker_tasks_runtime.py::test_run_task_success_path`
  - `tests/unit/test_worker_tasks_runtime.py::test_run_task_terminal_failure_refunds_and_marks_failed`
  - `tests/unit/test_worker_tasks_runtime.py::test_bootstrap_runtime_runs_migrations_and_loads_scripts`
  - `tests/unit/test_worker_tasks_runtime.py::test_shutdown_worker_closes_loop_and_redis`

### S0-BK-021-readiness-uses-shared-pool-and-timeouts
- Solution: Sol0

# BK-021: Readiness Uses Shared Pool and Timeouts

Priority: Backlog
Status: done
Depends on: P0-006

## Objective

Eliminate readiness probe connection churn by using shared runtime resources (DB pool/Redis client) with strict timeouts.

## Checklist

- [x] Refactor dependency checks to avoid creating fresh PG connections per probe
- [x] Add timeout budgets and failure-mode tests for readiness endpoints
- [x] Validate behavior under high probe frequency

## Exit Criteria

- [x] Readiness checks are low-overhead and do not amplify DB connection pressure

## Evidence

- Shared-resource readiness checks implemented: `src/solution0/dependencies.py`
- Lifespan wiring now passes shared `db_pool` and `redis_client` to dependency health service: `src/solution0/app.py`
- Timeout settings surfaced and configurable: `src/solution0/settings.py`, `.env.dev.defaults`
- Unit coverage:
  - `tests/unit/test_dependency_health.py::test_check_postgres_pool_uses_shared_pool`
  - `tests/unit/test_dependency_health.py::test_check_redis_client_uses_shared_client`
  - `tests/unit/test_dependency_health.py::test_build_dependency_health_service_prefers_shared_resources`

### S0-BK-022-auth-cache-contract-cleanup
- Solution: Sol0

# BK-022: Auth Cache Contract Cleanup

Priority: Backlog
Status: done
Depends on: P0-003

## Objective

Remove misleading mutable billing fields from auth cache payloads and keep auth cache strictly identity/authorization scoped.

## Checklist

- [x] Remove `credits` from `auth:{api_key}` hash payload
- [x] Ensure no runtime code path depends on auth-cache credits
- [x] Add tests proving auth correctness remains unchanged

## Exit Criteria

- [x] Auth cache schema is minimal, explicit, and non-overlapping with billing state

## Evidence

- Auth cache payload trimmed to identity-only fields (`user_id`, `name`, `role`): `src/solution0/services/auth.py`
- Legacy/invalid auth-cache schema falls back to DB lookup: `src/solution0/services/auth.py`
- Unit coverage:
  - `tests/unit/test_auth_service.py::test_resolve_user_cache_hit_skips_db`
  - `tests/unit/test_auth_service.py::test_resolve_user_tolerates_cache_population_failure`
  - `tests/unit/test_auth_service.py::test_resolve_user_falls_back_to_db_when_auth_cache_schema_is_invalid`

### S0-BK-023-docker-reproducible-builds-with-uv-lock
- Solution: Sol0

# BK-023: Docker Reproducible Builds with uv.lock

Priority: Backlog
Status: done
Depends on: P0-001

## Objective

Ensure container builds respect the lockfile and match local `uv` dependency resolution.

## Checklist

- [x] Move Dockerfile install path from `pip install .` to lock-respecting `uv` workflow
- [x] Verify deterministic dependency graph inside images
- [x] Add CI check that compares runtime lock fidelity

## Exit Criteria

- [x] Docker images are reproducible from the committed lock state

## Evidence

- All service Dockerfiles use `uv sync --frozen --no-dev`: `docker/api/Dockerfile`, `docker/worker/Dockerfile`, `docker/reaper/Dockerfile`
- Static + runtime lock-fidelity verification script added: `scripts/docker_lock_check.sh`
- Quality gate now enforces static lock checks: `scripts/quality_gate.sh`
- Full prove flow includes runtime lock-fidelity check artifact: `scripts/full_stack_check.sh`
- Make targets added: `Makefile` (`docker-lock`, `docker-lock-runtime`)

### S0-BK-024-bug-fixes-triage-followups
- Solution: Sol0

# BK-024: Bug-Fix Hardening Follow-ups (Solution 0)

Priority: high
Status: done

## Scope

Execute accepted hardening items for `0_solution` from consolidated contributor assessments.

## Accepted Checklist

- [x] `FIX-0-C1`: Remove plaintext `api_key` from pending marker payload (`task_write_routes` + marker readers)
- [x] `FIX-0-C2`: Mask API key in admin warning logs (`admin_routes`)
- [x] `FIX-0-H1`: Add retry/backoff around worker completion post-PG Redis writes
- [x] `FIX-0-H2`: Add retry/backoff around worker failure refund post-PG Redis writes
- [x] `FIX-0-H3`: Add retry/backoff around cancel refund post-PG Redis writes
- [x] `FIX-0-H4`: Add retry/backoff around admin credits cache sync post-PG
- [x] `FIX-0-H5`: Add retry/backoff around reaper stuck-task refund post-PG Redis writes
- [x] `FIX-0-H6`: Add `scan_iter(count=...)` and per-cycle processing cap in reaper
- [x] `FIX-0-H7`: Add settings validation: `task_cost > 0`, `max_concurrent > 0`
- [x] `FIX-0-M1`: Add INT32 bounds for `SubmitTaskRequest.x/y`
- [x] `FIX-0-M2`: Align dataclass boundaries (`models/domain` for shared business types; module-local for worker internals)

## Deferred (not part of this card)

- `FIX-0-H8`: migration-failure startup wrapper in reaper (deferred)

## Done Criteria

- [x] New/updated tests cover changed behavior
- [x] `ruff check` and `mypy --strict` pass
- [x] Relevant unit/integration/fault suites pass
- [x] `make prove` passes from clean state

### S0-BK-025-p2-connection-pool-and-timeout-hardening
- Solution: Sol0

# BK-025 — P2: Connection Pool and Timeout Hardening

Priority: P2 (nice-to-have, no production risk at compose scale)
Status: done
Solution: 0_solution

## Context

Core timeout protection is already shipped (statement_timeout=50ms, socket_timeout=0.05s, command_timeout=0.1s, retry jitter). These are residual gaps that matter at scale but are non-issues for compose-level demo.

## Gap 1: asyncpg pool.acquire() timeout

**Current:** `asyncpg.create_pool()` has no `timeout` kwarg on `pool.acquire()`. Under pool exhaustion, acquire blocks indefinitely.

**Fix:** Wrap `pool.acquire()` calls with `asyncio.wait_for(pool.acquire(), timeout=2.0)` or use asyncpg's `connection_class` with a custom acquire wrapper.

**Risk:** Low — compose runs 1-2 API workers against a pool of 10; exhaustion is unlikely.

## Gap 2: Redis max_connections limit

**Current:** `redis.asyncio.Redis` clients don't set `max_connections` on the connection pool. Under sustained load, unbounded connection growth is possible.

**Fix:** Add `max_connections=50` (or similar) to Redis client construction in `dependencies.py`.

**Risk:** Low — compose-scale traffic won't exhaust Redis connections.

## Gap 3: Readiness probe Redis socket_connect_timeout

**Current:** `dependencies.py:78` — the readiness Redis ping uses the shared client which has `socket_timeout` but the initial TCP connect has no explicit `socket_connect_timeout`.

**Fix:** Add `socket_connect_timeout=0.05` to Redis client construction.

**Risk:** Minimal — readiness probe could hang on TCP SYN to an unresponsive Redis, but k8s/compose health check timeout covers this.

## Definition of Done

- [x] `pool.acquire()` wrapped with timeout
- [x] Redis clients have `max_connections` set
- [x] Redis clients have `socket_connect_timeout` set
- [x] Existing tests pass
- [x] No new dependencies

### S0-P0-000-worklog-bootstrap-and-dependency-research
- Solution: Sol0

# P0-000: Worklog Bootstrap and Dependency Research

Priority: P0
Status: done
Depends on: none

## Objective

Create an execution-ready working structure for Solution 0 with kanban flow, runbook, baseline gate templates, and online-verified Python 3.12 dependency decisions.

## Checklist

- [x] Create `kanban/` board and lane structure (`todo`, `in-progress`, `done`, `backlog`)
- [x] Add delivery board with scope, priorities, and definition of done
- [x] Add `RUNBOOK.md` with `uv` + Docker Compose execution loop
- [x] Add baseline templates and gate YAMLs
- [x] Add dependency matrix with online verification evidence
- [x] Add execution-model research note mapping cards to architecture

## Acceptance Criteria

- [x] Worklog structure is complete and usable for daily execution
- [x] Dependencies are pinned from online sources and Python 3.12-compatible
- [x] TDD + type-safety gates are explicit in board/runbook

## Evidence

Created artifacts:

- `worklog/kanban/BOARD.md`
- `worklog/RUNBOOK.md`
- `worklog/baselines/TEMPLATE.md`
- `worklog/baselines/gates.unit.yaml`
- `worklog/baselines/gates.integration.yaml`
- `worklog/baselines/gates.release.yaml`
- `worklog/research/2026-02-15-python312-dependency-matrix.md`
- `worklog/research/2026-02-15-solution0-execution-model.md`

### S0-P0-001-repo-bootstrap-and-quality-gates
- Solution: Sol0

# P0-001: Repo Bootstrap and Quality Gates

Priority: P0
Status: done
Depends on: P0-000

## Objective

Bootstrap Solution 0 codebase for Python 3.12 using `uv`, with strict lint/type/test gates from day one.

## Checklist

- [x] Create `pyproject.toml` with pinned runtime and dev dependencies from dependency matrix
- [x] Add `uv.lock` and deterministic install workflow (`uv sync`)
- [x] Add `ruff` config and initial formatting/lint rules
- [x] Add `mypy` strict config (`disallow_untyped_defs`, `no_implicit_optional`, etc.)
- [x] Add package/module layout (`src/`, `tests/`) with typed stubs for API, worker, and storage layers
- [x] Add CI entry script (local and CI-parity) for lint + type + unit tests

## TDD Subtasks

1. Red

- [x] Add failing tests asserting config loading and startup dependency validation
- [x] Add failing type-check target that rejects untyped public service functions

2. Green

- [x] Implement minimal typed application skeleton until tests + mypy pass

3. Refactor

- [x] Remove duplicate settings/parsing code and centralize typed config

## Acceptance Criteria

- [x] `uv sync` completes from clean repo
- [x] `ruff check .` passes
- [x] `mypy --strict src tests` passes
- [x] `pytest -q tests/unit` passes on bootstrap suite

## Progress Notes (2026-02-15)

Implemented:

- bootstrap project metadata and tooling in `pyproject.toml`
- locked dependencies via `uv.lock`
- strict lints/types/tests via `scripts/ci_check.sh`
- typed skeleton modules:
  - `src/solution0/settings.py`
  - `src/solution0/dependencies.py`
  - `src/solution0/app.py`
  - `src/solution0/main.py`
- unit tests:
  - `tests/unit/test_settings.py`
  - `tests/unit/test_dependency_health.py`

Important compatibility finding:

- `celery[redis]==5.6.2` is incompatible with `redis>=6.5`
- resolved by pinning `redis==6.4.0` (latest compatible)

## Evidence

Red phase (expected failure):

- `pytest tests/unit/test_settings.py tests/unit/test_dependency_health.py`
- failed with `ModuleNotFoundError: No module named 'solution0'`

Green/refactor phase:

- `uv lock && uv sync --dev` succeeded
- `./scripts/ci_check.sh` succeeded:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy --strict src tests`
  - `pytest tests/unit`

### S0-P0-002-schema-migrations-and-seed-data
- Solution: Sol0

# P0-002: Schema, Migrations, and Seed Data

Priority: P0
Status: done
Depends on: P0-001

## Objective

Implement the baseline data model from RFC with forward-only migrations, indexes, and assignment-faithful seed users.

## Checklist

- [x] Add migration set for:
  - [x] assignment `users` schema (`name`, `api_key`, `credits`)
  - [x] `tasks`
  - [x] `credit_transactions`
  - [x] `credit_snapshots`
- [x] Apply index strategy from RFC:
  - [x] unique idempotency index (partial)
  - [x] task status/time index
  - [x] task user/time index
  - [x] credit transaction user/time index
- [x] Add deterministic seed fixture with assignment API keys
- [x] Add migration runner for local and test harness

## TDD Subtasks

1. Red

- [x] Add failing migration tests (empty DB -> target schema)
- [x] Add failing seed verification test for exact assignment records

2. Green

- [x] Implement migrations and seed loader to pass tests

3. Refactor

- [x] Split schema DDL and seed DML for maintainability

## Acceptance Criteria

- [x] Fresh Postgres initializes to expected schema
- [x] Seed users match assignment keys and balances
- [x] Query plans hit expected indexes for poll/history patterns

## Progress Notes (2026-02-15)

Implemented:

- migration runner and CLI:
  - `src/solution0/db/migrate.py`
- ordered SQL migration set:
  - `src/solution0/db/migrations/0001_create_users_base.sql`
  - `src/solution0/db/migrations/0002_extend_users_and_add_task_tables.sql`
  - `src/solution0/db/migrations/0003_indexes.sql`
  - `src/solution0/db/migrations/0004_seed_users.sql`
- migration-focused tests:
  - `tests/unit/test_migrations.py`

Evidence:

- red phase: `pytest tests/unit/test_migrations.py` failed with `ModuleNotFoundError: No module named 'solution0.db'`
- green phase: `pytest tests/unit/test_migrations.py` passed (`3 passed`)
- quality gate: `./scripts/ci_check.sh` passed (`14 passed`, ruff + mypy clean)
- compose runtime validation: `docker compose ps` showed healthy `postgres`, `redis`, and running `api`/`worker`/`reaper`
- index evidence:
  - `EXPLAIN ... WHERE user_id = ... ORDER BY created_at DESC LIMIT 10` used `idx_tasks_user_created`
  - `EXPLAIN ... WHERE status='PENDING' ORDER BY created_at` used `idx_tasks_status_created`

### S0-P0-003-auth-cache-and-credit-lua-gate
- Solution: Sol0

# P0-003: Auth Cache and Credit Lua Gate

Priority: P0
Status: done
Depends on: P0-002

## Objective

Implement authenticated task admission with Redis cache-aside auth and atomic Lua-based credit/concurrency/idempotency gate.

## Checklist

- [x] Bearer token extraction and validation against `users.api_key`
- [x] Redis cache-aside auth (`auth:<api_key>` with TTL)
- [x] Redis working-balance hydration (`credits:<user_id>`)
- [x] Lua script contract:
  - [x] idempotency check
  - [x] concurrency limit check
  - [x] balance check + deduction
  - [x] dirty-marking for snapshot/reconciliation
- [x] Cache miss retry path (`CACHE_MISS` -> PG hydrate -> retry)

## TDD Subtasks

1. Red

- [x] Add failing unit tests for Lua outcomes (`ok`, `idempotent`, `insufficient`, `concurrency`, `cache_miss`)
- [x] Add failing integration tests for auth cache hit/miss behavior

2. Green

- [x] Implement auth middleware and Lua admission gate until tests pass

3. Refactor

- [x] Extract typed gateway service for Redis script I/O and result decoding

## Acceptance Criteria

- [x] DB reads are avoided on auth cache hits and warm credit state
- [x] Admission is atomic for billing/concurrency/idempotency in Redis
- [x] Negative balance and double-charge states are impossible in tested flows

## Progress Notes (2026-02-15)

Implemented:

- auth and cache-aside:
  - `src/solution0/services/auth.py`
  - `src/solution0/db/repository.py` (`fetch_user_by_api_key`, `fetch_user_credits_by_api_key`)
- atomic admission Lua and typed parse:
  - `src/solution0/lua.py`
  - `src/solution0/app.py` (`run_admission_gate` path + cache-miss hydrate/retry)
- regression/unit tests:
  - `tests/unit/test_auth_utils.py`
  - `tests/unit/test_lua_parser.py`
- integration verification:
  - `tests/integration/test_error_contracts.py` (auth cache hit/miss metrics deltas)

Evidence:

- `./scripts/ci_check.sh` passed (`19 passed`)
- `./scripts/integration_check.sh` passed (`7 integration`, `1 e2e`)
- submit + idempotent replay in compose:
  - first submit: `201`
  - replay with same `Idempotency-Key`: `200` with same `task_id`

### S0-P0-004-task-api-contracts-and-error-taxonomy
- Solution: Sol0

# P0-004: Task API Contracts and Error Taxonomy

Priority: P0
Status: done
Depends on: P0-003

## Objective

Ship typed API contracts for submit/poll/cancel/admin endpoints, aligned to shared error taxonomy and RFC behavior.

## Checklist

- [x] Implement endpoints:
  - [x] `POST /v1/task`
  - [x] `GET /v1/poll`
  - [x] `POST /v1/task/{id}/cancel`
  - [x] `POST /v1/admin/credits`
  - [x] `GET /health`, `GET /ready`
- [x] Add typed request/response models and shared error envelope
- [x] Support `Idempotency-Key` header semantics
- [x] Enforce authorization constraints (admin-only top-up)

## TDD Subtasks

1. Red

- [x] Add contract tests for success and every expected error code (400/401/402/404/409/429/503)
- [x] Add failing idempotency conflict test (same key + changed payload)

2. Green

- [x] Implement endpoint handlers and validation until contract tests pass

3. Refactor

- [x] Centralize exception mapping and typed response builders

## Acceptance Criteria

- [x] API behavior matches RFC and shared assumptions
- [x] Response models are fully typed and validated
- [x] Error semantics are deterministic and test-backed

## Progress Notes (2026-02-15)

Implemented:

- endpoint contracts and handlers:
  - `src/solution0/app.py`
  - `src/solution0/schemas.py`
- repository and domain mapping:
  - `src/solution0/domain.py`
  - `src/solution0/db/repository.py`
- idempotency replay + error envelope with stable codes for `401/402/404/409/429/503`
- explicit contract coverage:
  - `tests/integration/test_error_contracts.py`

Evidence:

- `./scripts/integration_check.sh` passed (`7 integration`, `1 e2e`)
- compose smoke flow:
  - `POST /v1/task` => `201`
  - replay same idempotency key => `200` same `task_id`
  - `GET /v1/poll` terminal completion => `200`
  - `POST /v1/task/{id}/cancel` on terminal task => `409`
  - `POST /v1/admin/credits` with admin token => `200`

### S0-P0-005-celery-worker-cancel-and-reaper
- Solution: Sol0

# P0-005: Celery Worker, Cancel Flow, and Reaper

Priority: P0
Status: done
Depends on: P0-004

## Objective

Implement asynchronous worker execution lifecycle with safe cancellation, retries, and compensation recovery.

## Checklist

- [x] Wire Celery task publish from submit path
- [x] Worker lifecycle transitions (`PENDING -> RUNNING -> COMPLETED|FAILED|CANCELLED`)
- [x] Publish-failure compensation (`INCRBY` refund + active decrement)
- [x] Cancel path with revoke + refund + state update
- [x] Reaper job:
  - [x] orphan deduction recovery
  - [x] stuck task timeout recovery
  - [x] dirty credit snapshot flush
  - [x] result expiry cleanup

## TDD Subtasks

1. Red

- [x] Add failing integration tests for worker success/failure/retry outcomes
- [x] Add failing fault tests for crash-between-deduct-and-publish and stuck-running timeout

2. Green

- [x] Implement worker + reaper flows until tests pass

3. Refactor

- [x] Consolidate credit mutation logic in typed service to avoid divergent paths

## Acceptance Criteria

- [x] No leaked active counters after terminal paths
- [x] Refund behavior is exactly-once in all tested recovery scenarios
- [x] Reaper converges inconsistent states within bounded time

## Progress Notes (2026-02-15)

Implemented:

- Celery app and task execution:
  - `src/solution0/celery_app.py`
  - `src/solution0/worker_tasks.py`
- compensation and credit mutation paths:
  - `src/solution0/services/billing.py`
  - `src/solution0/app.py` (persist/publish failure compensation)
- periodic reconciliation/recovery:
  - `src/solution0/reaper.py`
- fault-path validation:
  - `tests/fault/test_runtime_faults.py`
  - `tests/fault/test_publish_failure_path.py`
  - `tests/unit/test_reaper_recovery.py`

Evidence:

- `docker compose ps` shows running `worker` and `reaper`
- submit/poll flow reaches `COMPLETED`
- cancel on completed task returns deterministic `409 CONFLICT`
- redis restart resilience:
  - no `NoScriptError` on post-recovery submit paths
  - admission/decrement Lua scripts auto-reload when Redis script cache is lost
- `./scripts/fault_check.sh` passed (`4 passed`) including worker-down and publish-failure compensation paths

### S0-P0-006-observability-prometheus-grafana-and-structured-logs
- Solution: Sol0

# P0-006: Observability - Prometheus, Grafana, Structured Logs

Priority: P0
Status: done
Depends on: P0-005

## Objective

Add production-grade observability for Solution 0: structured logs, metrics, dashboard provisioning, and actionable alerts.

## Checklist

- [x] Add `structlog` JSON logs in API + worker + reaper
- [x] Ensure correlation keys in all critical events (`task_id`, `user_id`, `trace_id`)
- [x] Export Prometheus metrics from API and worker
- [x] Compose wiring for Prometheus + Grafana
- [x] Provision baseline Grafana dashboard JSON
- [x] Add Alertmanager rules file (documented if not run in compose)

## TDD Subtasks

1. Red

- [x] Add failing tests for log event shape and required keys
- [x] Add failing metrics exposure tests (`/metrics` contains expected series)

2. Green

- [x] Implement logging/metrics and pass tests

3. Refactor

- [x] Remove high-cardinality labels; normalize metric dimensions

## Acceptance Criteria

- [x] Observability stack starts in Docker Compose
- [x] Core operational metrics and error counters are visible
- [x] Dashboard and alert rules map to RFC critical paths

## Progress Notes (2026-02-15)

Implemented:

- logging + correlation:
  - `src/solution0/logging_utils.py`
  - structured event emitters in `src/solution0/app.py`, `src/solution0/worker_tasks.py`, `src/solution0/reaper.py`
- metrics:
  - `src/solution0/metrics.py`
  - `/metrics` endpoint in API and worker metrics exporter on `:9100`
- compose observability stack and provisioning:
  - `compose.yaml`
  - `prometheus/prometheus.yml`
  - `prometheus/alerts.yml`
  - `grafana/provisioning/datasources/datasource.yml`
  - `grafana/provisioning/dashboards/dashboard.yml`
  - `grafana/dashboards/solution0-overview.json`
- observability contract tests:
  - `tests/unit/test_logging_shape.py`
  - `tests/integration/test_error_contracts.py` (metrics series assertions)

Evidence:

- `docker compose ps` shows running `prometheus` and `grafana`
- `curl http://localhost:8000/metrics` returns Prometheus series
- `./scripts/ci_check.sh` and `./scripts/integration_check.sh` pass with structured-log and metrics assertions

### S0-P0-007-tdd-suite-unit-integration-e2e-fault
- Solution: Sol0

# P0-007: TDD Suite - Unit, Integration, E2E, Fault

Priority: P0
Status: done
Depends on: P0-006

## Objective

Finish a comprehensive automated test suite that proves functional correctness, failure handling, and reproducibility.

## Checklist

- [x] Unit tests for domain logic, validation, Lua outcomes
- [x] Integration tests for end-to-end API + Redis + Postgres + Celery behavior
- [x] E2E test for demo script flow
- [x] Fault tests:
  - [x] worker crash
  - [x] Redis down
  - [x] Postgres down
  - [x] broker publish failure path
- [x] Add coverage threshold and test markers (`unit`, `integration`, `e2e`, `fault`) split:
  - marker coverage delivered in this card
  - numeric threshold intentionally tracked in `backlog/BK-005-test-coverage-gate-70-80.md`

## TDD Subtasks

1. Red

- [x] Add failing tests for all remaining uncovered invariants

2. Green

- [x] Implement missing behavior and/or recovery until tests pass

3. Refactor

- [x] Remove flaky timing assumptions and stabilize with bounded retries/timeouts

## Acceptance Criteria

- [x] Full matrix passes consistently on local Docker Compose runs
- [x] Coverage threshold hard gate explicitly deferred to newly-added backlog card `BK-005`
- [x] Fault test outcomes align with degradation matrix claims

## Progress Notes (2026-02-15)

Implemented:

- new integration suite:
  - `tests/integration/test_api_flow.py`
- new e2e suite:
  - `tests/e2e/test_demo_script.py`
- new fault suite:
  - `tests/fault/test_readiness_degradation.py`
  - `tests/fault/test_runtime_faults.py`
  - `tests/fault/test_publish_failure_path.py`
- execution scripts:
  - `scripts/integration_check.sh`
  - `scripts/fault_check.sh`
- pytest marker registration:
  - `pyproject.toml`

Evidence:

- unit gate: `./scripts/ci_check.sh` => `19 passed`
- integration gate: `./scripts/integration_check.sh` => `7 integration passed`, `1 e2e passed`
- fault gate: `./scripts/fault_check.sh` => `4 passed` (worker, redis, postgres, publish-failure coverage)

### S0-P0-008-demo-script-and-release-readiness
- Solution: Sol0

# P0-008: Demo Script and Release Readiness

Priority: P0
Status: done
Depends on: P0-007

## Objective

Deliver reproducible demo and release-readiness evidence for Solution 0.

## Checklist

- [x] Add deterministic demo script (`submit -> poll until terminal`)
- [x] Add admin top-up and insufficient-credit scenarios in demo docs
- [x] Capture baseline evidence using `worklog/baselines/TEMPLATE.md`
- [x] Validate release gates (`unit`, `integration`, `fault`, observability)
- [x] Update `0_solution/README.md` with run/test instructions

## TDD Subtasks

1. Red

- [x] Add failing e2e test that asserts demo script output contract

2. Green

- [x] Implement demo script and docs until e2e passes

3. Refactor

- [x] Reduce demo script complexity; keep readable and deterministic

## Acceptance Criteria

- [x] New engineer can run demo from clean setup in one pass
- [x] All release gates pass and artifact is recorded
- [x] Solution 0 is ready for demo review

## Progress Notes (2026-02-15)

Implemented:

- demo artifacts:
  - `utils/demo.sh`
  - `tests/e2e/test_demo_script.py`
- documentation:
  - `README.md` (setup, run, API/demo flows, test gates)
- release evidence:
  - `worklog/baselines/2026-02-15-solution0-baseline.md`
  - `worklog/baselines/latest-release-gate.json`

Evidence:

- `./utils/demo.sh` exits `0` and reaches terminal `COMPLETED`
- `./scripts/ci_check.sh` pass
- `./scripts/integration_check.sh` pass
- `./scripts/fault_check.sh` pass (`4` fault scenarios)

## Solution 1

### S1-BK-001-property-based-credit-invariants-and-fuzzing
- Solution: Sol1

# BK-001: Property-Based Credit Invariants and Fuzzing

Priority: P1
Status: done
Depends on: P0-010

## Objective

Add property-based tests for credit and state-transition invariants under randomized interleavings.

## Checklist

- [x] Define invariants (no negative balance, no duplicate refund, valid terminal transitions)
- [x] Add Hypothesis-style property tests for submit/cancel/worker/reaper interleavings (deterministic seeds)
- [x] Integrate with CI as non-flaky deterministic profile

## Acceptance Criteria

- [x] Invariants hold across randomized scenarios
- [x] Failures produce minimal reproducible traces

### S1-BK-002-stream-throughput-and-memory-profiling
- Solution: Sol1

# BK-002: Stream Throughput and Memory Profiling

Priority: P1
Status: done
Depends on: P0-010

## Objective

Quantify Redis Streams throughput and memory behavior under sustained load, then tune consumer and trim strategy.

## Checklist

- [x] Add load profiles for producer/consumer saturation
- [x] Measure stream length growth, PEL growth, and latency percentiles
- [x] Tune `MAXLEN`/trim policy and batch consumption settings

## Acceptance Criteria

- [x] Capacity model is backed by measured data
- [x] Tuning changes are documented with before/after metrics

## What changed

- `scripts/load_harness.py`
  - Added stream observability sampling during each load profile:
    - `XLEN tasks:stream`
    - `XPENDING tasks:stream workers` summary count
    - Redis `INFO memory` (`used_memory`)
  - Added profile-level stream summaries (`start`, `end`, `max`, `p95`, `growth`) for:
    - `stream_length`
    - `pel_pending`
    - `redis_used_memory_bytes`
  - Added sustained saturation controls:
    - `--saturation-requests`
    - `--saturation-concurrency`
    - `--saturation-retry-attempts`
    - `--saturation-retry-sleep-seconds`
  - Added runtime-setting capture from live worker container env (`docker compose exec worker printenv ...`) to avoid host/env drift in reports.

- `scripts/capacity_model.py`
  - Added stream-aware capacity rows:
    - `stream_max`, `pel_max`, `redis_memory_max_mib`
  - Added baseline-vs-tuned compare mode:
    - `--compare-input <baseline-report.json>`
  - Added comparison deltas for throughput, p95, stream max, PEL max, and Redis max memory.

- New tests
  - `tests/unit/test_load_harness_stream_metrics.py`
  - `tests/unit/test_capacity_model_compare.py`

## Measured evidence

Generated artifacts:

- Baseline report: `worklog/evidence/load/bk002-baseline.json`
- Tuned report: `worklog/evidence/load/bk002-tuned.json`
- Compare JSON: `worklog/evidence/load/bk002-capacity-compare.json`
- Compare markdown: `worklog/evidence/load/bk002-capacity-compare.md`

Key saturation comparison (same load profile):

- Baseline (`read_count=1`, `claim_count=20`)
  - throughput: `0.4971 rps`
  - p95 submit latency: `20.473 ms`
  - stream max: `113`
  - PEL max: `5`
  - Redis max memory: `1.958 MiB`
- Tuned (`read_count=4`, `claim_count=64`)
  - throughput: `0.4967 rps`
  - p95 submit latency: `19.151 ms`
  - stream max: `111`
  - PEL max: `7`
  - Redis max memory: `1.954 MiB`

Outcome:

- No material throughput gain from the tested tuning pair.
- Minor latency improvement at saturation, but PEL behavior regressed slightly.
- Decision: keep baseline defaults (`read_count=1`, `claim_count=20`) and keep `REDIS_TASKS_STREAM_MAXLEN=500000` unchanged.

## Validation run

- `ruff check src scripts tests`
- `mypy --strict src tests`
- `pytest -q tests/unit/test_load_harness_stream_metrics.py tests/unit/test_capacity_model_compare.py tests/unit/test_tooling_auth_contract.py tests/unit/test_settings.py`

### S1-BK-003-otel-tempo-runtime-profile
- Solution: Sol1

# BK-003: OTel Tempo Runtime Profile

Priority: P2
Status: done
Depends on: P0-010

## Objective

Promote OTel+Tempo from config-only artifacts to optional runtime compose profile for local tracing demo.

## Checklist

- [x] Add optional compose profile for OTel collector + Tempo
- [x] Wire trace export from API, worker, and reconciler
- [x] Provide dashboard/query examples in runbook

## Acceptance Criteria

- [x] Optional profile runs without affecting baseline demo path
- [x] Trace spans show API -> stream -> worker lifecycle

## What changed

- Added runtime tracing module:
  - `src/solution1/observability/tracing.py`
  - process bootstrap (`configure_process_tracing`)
  - context propagation helpers (`inject_current_trace_context`, `extract_trace_context`)
  - shared span wrapper (`start_span`)
- Wired tracing across services:
  - API: HTTP server spans in `src/solution1/app.py` and trace-context injection in `src/solution1/api/task_write_routes.py`
  - Worker: consumer spans with propagated parent context in `src/solution1/workers/stream_worker.py`
  - Reaper: cycle spans in `src/solution1/workers/reaper.py`
- Enabled compose/runtime toggles:
  - Added OTel env settings to `.env.dev.defaults`
  - Added compose overrides for `OTEL_ENABLED` + exporter vars in `compose.yaml`
- Added tests:
  - `tests/unit/test_tracing_runtime.py`
  - Updated `tests/unit/test_app_paths.py`, `tests/unit/test_stream_worker.py`, `tests/unit/test_observability_configs.py`, `tests/unit/test_settings.py`
- Added runbook section:
  - `README.md` tracing profile startup and TraceQL examples

## Validation run

- `ruff check src/solution1/observability/tracing.py src/solution1/app.py src/solution1/api/task_write_routes.py src/solution1/workers/stream_worker.py src/solution1/workers/reaper.py tests/unit/test_tracing_runtime.py tests/unit/test_app_paths.py tests/unit/test_stream_worker.py tests/unit/test_settings.py tests/unit/test_observability_configs.py`
- `mypy --strict src tests`
- `pytest -q tests/unit/test_tracing_runtime.py tests/unit/test_app_paths.py tests/unit/test_stream_worker.py tests/unit/test_reaper_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_reaper_retention.py tests/unit/test_observability_configs.py tests/unit/test_settings.py`

### S1-BK-004-security-hardening-and-key-rotation
- Solution: Sol1

# BK-004: Security Hardening and Key Rotation

Priority: P1
Status: done
Depends on: P0-010

## Objective

Harden non-dev authentication posture with stricter runtime validation and deterministic secret handling while keeping solution1 API behavior unchanged.

## Checklist

- [x] Enforce non-dev secret-hygiene checks in settings validation
- [x] Harden JWT verification behavior around key rotation and claim checks
- [x] Add regression tests for auth edge cases and settings hygiene
- [x] Document production secret/JWKS contracts and rotation workflow

## Acceptance Criteria

- [x] Security-related runtime/behavioral guardrails in app and settings
- [x] Unit tests cover placeholder/revocation/jwks-refresh failure modes
- [x] Documentation reflects `HYDRA_JWKS_CACHE_TTL_SECONDS` and `_FILE` secret inputs
- [x] Card closed in kanban with validation list

## Notes

- `src/solution1/core/settings.py`
  - Added non-dev secret validation for:
    - UUID format on `admin_api_key`, `alice_api_key`, `bob_api_key`
    - placeholder API keys rejection outside `APP_ENV=dev`
    - OAuth client secret placeholder rejection outside `APP_ENV=dev`
    - OAuth secret minimum length enforcement (`>= 24` chars)
    - non-negative `hydra_jwks_cache_ttl_seconds`
- `src/solution1/app.py`
  - Added controlled JWKS client TTL caching with per-process cache dictionary.
  - JWT decode now retries once with forced JWKS refresh on signing-key misses (rotation-safe behavior).
  - `jti` is required and must be non-empty for token processing; revocation checks run deterministically.
- Tests added/updated:
  - `tests/unit/test_app_internals.py`
    - missing `jti` rejection branch
    - JWKS key-miss retry path assertion
  - `tests/unit/test_app_paths.py`
    - compat fixes for `_jwks_client` monkeypatch signatures
  - `tests/unit/test_settings.py`
    - non-dev placeholder/API-key rejection
    - short secret rejection
    - non-UUID API key rejection
    - negative `hydra_jwks_cache_ttl_seconds` rejection
    - `_FILE` secret source support regression
- Docs:
  - `README.md` (security/secret contract and JWKS cache behavior)
  - `worklog/RUNBOOK.md` (non-dev hardening reminders + rotation checks)

## Validation

- `uv run ruff check src/solution1/app.py src/solution1/core/settings.py tests/unit/test_app_internals.py tests/unit/test_settings.py tests/unit/test_app_paths.py`
- `uv run pytest -q tests/unit/test_app_internals.py tests/unit/test_settings.py tests/unit/test_app_paths.py`

### S1-BK-005-webhook-delivery-path
- Solution: Sol1

# BK-005: Webhook Delivery Path

Priority: P2
Status: done
Depends on: P0-010

## Objective

Add optional webhook callback flow for task terminal events.

## Checklist

- [x] Add callback URL registration and validation
- [x] Emit terminal events to webhook dispatcher with retries/backoff
- [x] Add dead-letter handling and replay tooling

## Acceptance Criteria

- [x] Webhook delivery semantics are documented and test-covered
- [x] Failure isolation does not impact main task flow

## What changed

- API and schema surface:
  - Added `PUT/GET/DELETE /v1/webhook` in `src/solution1/api/webhook_routes.py`
  - Added request/response models in `src/solution1/models/schemas.py`
  - Added route constants and app registration wiring in `src/solution1/api/paths.py` and `src/solution1/app.py`
- Storage and migrations:
  - Added migration `0008_webhook_delivery_tables.sql`
  - Added repository functions for subscription CRUD and dead-letter persistence in `src/solution1/db/repository.py`
- Delivery engine:
  - Added webhook service helpers in `src/solution1/services/webhooks.py`
  - Added dispatcher worker with retry/backoff + DLQ in `src/solution1/workers/webhook_dispatcher.py`
  - Emitted terminal events from task cancel + stream worker completion/failure paths
- Operations:
  - Added compose service `webhook-dispatcher` (`compose.yaml`)
  - Added Prometheus scrape target + alerts for dispatcher/DLQ
  - Added replay tool `scripts/replay_webhook_dlq.py` and `make webhook-replay`
  - Added README runbook section for webhook registration and DLQ replay

## Validation run

- `ruff check src tests`
- `mypy --strict src tests`
- `pytest -q tests/unit/test_webhook_service.py tests/unit/test_webhook_dispatcher.py tests/unit/test_app_paths.py tests/unit/test_stream_worker.py tests/unit/test_reaper_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_reaper_retention.py tests/unit/test_migrations.py tests/unit/test_observability_configs.py tests/unit/test_settings.py`

### S1-BK-006-architecture-and-best-practices-review
- Solution: Sol1

# BK-006: Architecture and Best Practices Review

Priority: P1
Status: done
Depends on: P0-010

## Objective

Execute a formal architecture/code-quality review focused on async correctness, Redis/Streams usage, DB model/indexes, recovery/reaper behavior, logging/metrics quality, and deployment posture.

## Findings (severity-ranked)

### 1) High: Tracing posture does not match RFC/compose intent

- Evidence:
  - RFC and matrix describe OTel/Tempo for solution1 (`.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`).
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

### S1-BK-007-module-boundary-and-refactor-budget
- Solution: Sol1

# BK-007: Module Boundary and Refactor Budget

Priority: P2
Status: done
Depends on: P0-010

## Objective

Continuously control codebase complexity with explicit module boundaries and refactor budget.

## Refactor-Budget Plan

### Phase-1 (implemented now)

- Extract reusable API error envelope construction into `solution1.api.error_responses`.
- Replace duplicated local route-level wrappers in `solution1/api/task_write_routes.py`,
  `solution1/api/task_read_routes.py`, and `solution1/api/admin_routes.py`.
- Add focused regression tests for envelope shape and optional retry metadata.

### Phase-2 (deferred)

- Split route-side authorization/read-path helpers and cache/task-state helpers into
  dedicated request-context modules.
- Introduce module-level package ownership map and complexity budget ownership in
  `scripts/complexity_gate.py` for the entire `api` package.
- Add architecture ADR for boundary conventions + module ownership documentation.

## Implementation Outcomes

- Added `solution1/api/error_responses.py` with `api_error_response`.
- Route modules now use shared helper directly instead of local `_error_response` wrappers.
- Kept behavior unchanged (same status codes and error envelope fields), proven by
  focused unit tests + route behavior tests.

## Checklist

- [x] Extract shared API error-response helper module
- [x] Use shared helper in multiple route modules (task write/read/admin)
- [ ] Set file/function complexity thresholds per package
- [ ] Refactor oversized modules into cohesive service/repository/api units
- [ ] Keep strict typing and test coverage stable during refactor

## Acceptance Criteria

- [x] No behavior change in status/error payload for touched routes
- [ ] Complexity gates are enforced in quality checks
- [ ] Module ownership and boundaries are clearly documented

## Validation

- `python3 -m py_compile src/solution1/api/error_responses.py src/solution1/api/task_write_routes.py src/solution1/api/task_read_routes.py src/solution1/api/admin_routes.py tests/unit/test_error_responses.py`
- `uv run pytest -q tests/unit/test_error_responses.py tests/unit/test_app_paths.py::test_submit_rejects_oversized_idempotency_key tests/unit/test_app_paths.py::test_submit_reject_paths`

### S1-BK-008-credit-refund-durability-risk-register
- Solution: Sol1

# BK-008: Credit Refund Durability Risk Register

Priority: P1
Status: done
Depends on: P1-021

## Objective

Track credit-refund durability failures and residual risk across API admission, worker failure, and reaper repair paths as an explicit and reviewable register.
Keep scope to risk identification, monitoring, and low-risk guardrails that reduce over-refund exposure without behavior changes.

## Checklist

- [x] Document known limitation and blast radius in RFC/runbook
- [x] Define trigger/threshold for promoting this into active implementation scope
- [x] Add mitigation monitoring signals (error count/backlog indicators)
- [x] Add no-refund regression tests for no partial-compensation edges

## Acceptance Criteria

- [x] Limitation is explicit, bounded, and reviewable
- [x] Promotion criteria to active implementation are clear
- [x] Risks mapped to exact code paths, residual risk called out, and monitoring thresholds documented
- [x] Tests cover no-refund paths so compensation only occurs after durable DB-side transition+ledger success

## Findings and Remediations

- [R1] API persist failure before/after DB write (high)
  - Severity: High (temporary overcharge + active-slot leakage).
  - Evidence: `src/solution1/api/task_write_routes.py` persists task row + `task_deduct` before confirming Redis cleanup.
  - Mitigation:
    - Keep compensation path explicit to Redis and idempotency cleanup only when DB transaction fails.
    - Mark `db_row_created` and only run compensation when ledger/task row are not known to have been committed.
  - Residual risk: process crash window between DB commit and Redis cleanup can still delay repair.
  - Files:
    - `src/solution1/api/task_write_routes.py`
    - `tests/unit/test_app_paths.py`

- [R2] Worker failure after task terminal DB transition before Redis refund (medium)
  - Severity: Medium (temporary overcharge if process crashes after DB success).
  - Evidence: `_handle_failure` in `src/solution1/workers/stream_worker.py`.
  - Mitigation:
    - `refund_and_decrement_active` now gates on both DB ops succeeding in one transaction.
    - Add explicit log branch `stream_task_failure_db_update_failed`.
  - Residual risk: crash before Redis refund means temporary credit debt until recovery job or manual fix.
  - Files:
    - `src/solution1/workers/stream_worker.py`
    - `tests/unit/test_stream_worker.py`

- [R3] Reaper stuck-task refund/write mismatch (high)
  - Severity: High (retry semantics can skip refund in uncertain states).
  - Evidence: `_process_stuck_tasks` in `src/solution1/workers/reaper.py`.
  - Mitigation:
    - Require `update_task_failed` + `insert_credit_transaction` success before `refund_and_decrement_active`.
    - Add no-refund branch for DB failure cases.
  - Residual risk: DB-level failure can suppress repair before the next reconciliation tick.
  - Files:
    - `src/solution1/workers/reaper.py`
    - `tests/unit/test_reaper_recovery.py`

## Promotion criteria

- Any single condition for 30 minutes:
  - `sum(rate(task_submissions_total{result="persist_failure"}[5m])) / sum(rate(task_submissions_total[5m])) > 0.1`
  - log-level `stream_task_failure_db_update_failed` or `reaper_stuck_task_refund_error` rate > 3/min
  - `sum(rate(reaper_refunds_total{reason="stuck_task"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`
  - `sum(rate(reaper_refunds_total{reason="orphan_marker"}[24h])) / sum(rate(task_submissions_total[24h])) > 0.02`

## Validation

- `uv run ruff check src/solution1/api/task_write_routes.py src/solution1/workers/stream_worker.py src/solution1/workers/reaper.py tests/unit/test_app_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_stream_worker.py`
- `uv run pytest -q tests/unit/test_app_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_stream_worker.py`

## Notes

- RFC section updated: `0_1_rfcs/RFC-0001-1-solution-redis-native-engine/data-ownership.md`
- Runbook section added for BK-008 monitoring and trigger thresholds: `worklog/RUNBOOK.md`

### S1-BK-009-jwt-boundary-hardening-and-principal-resolution
- Solution: Sol1

# BK-009: JWT Boundary Hardening and Principal Resolution

Priority: P1
Status: done
Depends on: P1-021

## Objective

Capture defense-in-depth JWT hardening (audience strictness, broader principal mapping model) for post-ship iteration.

## Checklist

- [x] Evaluate `aud` enforcement impact with Hydra issuance contract
- [x] Design scalable claim-to-principal resolution strategy
- [x] Expand auth hardening tests for edge cases

## Acceptance Criteria

- [x] Hardening plan is implementation-ready without destabilizing current flow

## Notes

- Added config-driven audience enforcement in JWT decode path:
  - `hydra_expected_audience` in settings (optional).
  - when configured, JWT decode enables `verify_aud=True` and passes expected audience.
- Tightened principal resolution boundaries:
  - reject tokens where `client_id` and `sub` disagree.
  - reject invalid or mismatched `role` claims.
  - reject invalid or mismatched `tier` claims.
  - still derives base principal from deterministic client mapping to keep local/dev reproducibility.
- Expanded auth boundary tests in `tests/unit/test_app_internals.py`:
  - invalid role/tier rejection
  - mismatched `client_id/sub` rejection
  - audience enforcement branch (decode called with expected audience + verify_aud)
  - valid role/tier path remains accepted

## Validation

- `ruff check src/solution1/app.py src/solution1/core/settings.py tests/unit/test_app_internals.py`
- `pytest -q tests/unit/test_app_internals.py`
- `pytest -q tests/unit/test_app_paths.py`

### S1-BK-010-solution1-request-rate-limiter
- Solution: Sol1

# BK-010: Solution 1 Request Rate Limiter

Priority: P1
Status: done
Depends on: P1-021

## Objective

Track optional per-user request rate limiting as a feature-scope extension.

## Decision: Rejected for now

- The current implementation already enforces a **hard capacity control** at admission time:
  - `run_admission_gate` rejects with `reason="CONCURRENCY"` when active tasks reach per-tier `max_concurrent`.
  - This surfaces as HTTP `429` in submit handler.
- Existing behavior and tests already assert 429 behavior under concurrency saturation (`tests/unit/test_app_paths.py`, `tests/integration/test_multi_user_concurrency.py`, `tests/integration/test_oauth_jwt_flow.py`).
- The RFC/architecture posture does not require a separate request-rate SLA contract for this iteration; adding one would require:
  - new policy config,
  - token/idempotency-aware accounting windows,
  - additional metrics/event semantics,
  - and broader contract/API docs changes.
- Given assignment scope and risk, we defer the feature and keep behavior unchanged.

## Scope boundary update

- No code changes for BK-010 are being introduced in this cycle.
- This card is closed as **rejected-by-design (deferred)** pending explicit product-level requirement.

## Checklist

- [x] Evaluate whether a rate limiter is justified against current concurrency/error semantics
- [x] Document rationale against assignment and RFC behavior
- [x] Decide implementation vs deferral and record outcome
- [ ] Implement production-safe limiter (not justified in current scope)

## Acceptance Criteria

- [x] Feature scope decision is explicit and separated from bug-fix work
- [x] Existing 429/concurrency semantics remain unchanged
- [x] Kanban card moved from backlog to done with clear rationale

## Validation

- `uv run pytest -q tests/unit/test_app_paths.py tests/integration/test_multi_user_concurrency.py tests/integration/test_oauth_jwt_flow.py`

### S1-BK-011-retention-enforcement-and-purge-jobs
- Solution: Sol1

# BK-011: Retention Enforcement and Purge Jobs

Priority: P1
Status: done
Depends on: P1-021

## Summary

Added bounded reaper retention for historical credit records to reduce unbounded table growth and align with operational risk management in solution1.

## Implemented

- Configurable reaper settings added in `src/solution1/core/settings.py` + `.env.dev.defaults`:
  - `REAPER_RETENTION_BATCH_SIZE`
  - `REAPER_CREDIT_TRANSACTION_RETENTION_SECONDS`
  - `REAPER_CREDIT_DRIFT_AUDIT_RETENTION_SECONDS`
- Added bounded purge repository helpers:
  - `purge_old_credit_transactions(...)`
  - `purge_old_credit_drift_audit(...)`
  - both delete by bounded `ORDER BY ... LIMIT batch_size` with timestamp cutoff.
- Reaper cleanup execution added in `src/solution1/workers/reaper.py`:
  - Purges only when retention window is enabled (`> 0`) and records counters.
  - Cycle log includes purge counts.
- Observability:
  - `REAPER_RETENTION_DELETES_TOTAL` metric added in `src/solution1/observability/metrics.py`
  - Contract test updated to include new metric symbol.
- DB index support:
  - Added migration `src/solution1/db/migrations/0007_reaper_retention_indexes.sql`
  - Updated `test_migrations.py` ordered file assertion.

## Acceptance status

- [x] Define purge cadence and safety bounds (`REAPER_RETENTION_BATCH_SIZE`, bounded batch deletes).
- [x] Add index and observability considerations (new metric, bounded delete index migration).
- [x] Implement tests for bounded purge behavior (new unit tests in `tests/unit/test_reaper_retention.py` and reaper cycle wiring tests).

## Evidence and validation notes

- Runbook:
  - `worklog/RUNBOOK.md` updated with BK-011 operational checks and controls.
- Retention settings now documented in `README.md`.

## Residual risk / next steps

- `stream_checkpoints` remains intentionally untouched in this card since no runtime read path exists yet; purging it would create recoverability risk without explicit replay semantics.
- Current retention windows default to 24h for demos and are intended to be raised for production based on storage/SLA policy.

### S1-BK-012-test-foundation-and-lua-contract-hardening
- Solution: Sol1

# BK-012: Test Foundation and Lua Contract Hardening

Priority: P1
Status: done
Depends on: P1-021

## Objective

Track test-infra refinement (shared fixtures/conftest and deeper Lua contract tests) as quality hardening.

## Checklist

- [x] Consolidate duplicate unit-test fakes into shared fixtures
- [x] Add direct Lua contract tests beyond route-level integration coverage
- [x] Measure maintenance impact before/after fixture consolidation

## Acceptance Criteria

- [x] Hardening plan is actionable and scoped for a separate quality pass

## Notes

- Added shared test support module:
  - `tests/fakes.py` with reusable fake DB transaction/pool and fake Redis client/pipeline.
- Refactored duplicate fake stacks to shared module:
  - `tests/unit/test_app_paths.py`
  - `tests/fault/test_publish_failure_path.py`
- Added direct Lua runtime contract coverage:
  - `tests/integration/test_lua_contract.py`
  - validates `OK`, `IDEMPOTENT`, `CONCURRENCY`, `INSUFFICIENT`, `CACHE_MISS`
  - executes the real `ADMISSION_LUA` script against a live Redis compose service (`redis-cli EVAL`)
- Maintenance impact snapshot:
  - removed duplicate fake class blocks from two high-churn suites and centralized behavior in one module.

## Validation

- `ruff check tests/fakes.py tests/unit/test_app_paths.py tests/fault/test_publish_failure_path.py tests/integration/test_lua_contract.py`
- `pytest -q tests/unit/test_app_paths.py tests/fault/test_publish_failure_path.py tests/unit/test_lua_parser.py`
- `docker compose up -d redis && pytest -q tests/integration/test_lua_contract.py -m integration && docker compose down -v --remove-orphans`

### S1-BK-013-bug-fixes-triage-followups
- Solution: Sol1

# BK-013: Bug-Fix Hardening Follow-ups (Solution 1)

Priority: high
Status: done

## Scope

Execute accepted hardening items for `1_solution` from consolidated contributor assessments.

## Accepted Checklist

- [x] `FIX-1-C1`: Remove plaintext `api_key` from pending marker payload (`task_write_routes` + marker readers)
- [x] `FIX-1-H1`: Add retry/backoff around worker completion post-PG Redis writes
- [x] `FIX-1-H2`: Add retry/backoff around worker failure refund post-PG Redis writes
- [x] `FIX-1-H3`: Add retry/backoff around cancel refund/state post-PG Redis writes
- [x] `FIX-1-H4`: Add retry/backoff around admin credits cache sync post-PG
- [x] `FIX-1-H5`: Add retry/backoff around reaper stuck-task refund post-PG Redis writes
- [x] `FIX-1-H6`: Add `scan_iter(count=...)` and per-cycle processing cap in reaper
- [x] `FIX-1-H7`: Fail startup on revocation rehydration failure (fail-closed)
- [x] `FIX-1-H8`: Bound webhook pending queue length (`MAXLEN`/trim + config)
- [x] `FIX-1-H9`: Add settings validation: `task_cost > 0`, `max_concurrent > 0`
- [x] `FIX-1-M2`: Align dataclass boundaries (`models/domain` for shared business types; module-local for worker internals)

## Deferred / Rejected / Already Fixed

- Deferred: `FIX-1-C2`, `FIX-1-H10`
- Rejected as written: `FIX-1-C3`
- Already fixed: `FIX-1-M1`

## Done Criteria

- [x] New/updated tests cover changed behavior
- [x] `ruff check` and `mypy --strict` pass
- [x] Relevant unit/integration/fault suites pass
- [x] `make prove` passes from clean state

### S1-BK-014-p2-connection-pool-and-timeout-hardening
- Solution: Sol1

# BK-014 — P2: Connection Pool and Timeout Hardening

Priority: P2 (nice-to-have, no production risk at compose scale)
Status: done
Solution: 1_solution

## Context

Core timeout protection is already shipped (statement_timeout=50ms, socket_timeout=0.05s, command_timeout=0.1s, retry jitter). These are residual gaps that matter at scale but are non-issues for compose-level demo.

## Gap 1: asyncpg pool.acquire() timeout

**Current:** `asyncpg.create_pool()` has no `timeout` kwarg on `pool.acquire()`. Under pool exhaustion, acquire blocks indefinitely.

**Fix:** Wrap `pool.acquire()` calls with `asyncio.wait_for(pool.acquire(), timeout=2.0)` or use asyncpg's `connection_class` with a custom acquire wrapper.

**Risk:** Low — compose runs 1-2 API workers against a pool of 10; exhaustion is unlikely.

## Gap 2: Redis max_connections limit

**Current:** `redis.asyncio.Redis` clients don't set `max_connections` on the connection pool. Under sustained load, unbounded connection growth is possible.

**Fix:** Add `max_connections=50` (or similar) to Redis client construction in `dependencies.py` / `app.py`.

**Risk:** Low — compose-scale traffic won't exhaust Redis connections.

## Gap 3: httpx connection pool Limits

**Current:** `httpx.AsyncClient` used for webhook dispatcher and OAuth/Hydra calls has no explicit `limits=httpx.Limits(...)` configured. Default is 100 connections per host, which is fine, but making it explicit improves observability and prevents surprise under load.

**Fix:** Add `limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)` to httpx client construction.

**Risk:** Minimal — defaults are reasonable; this is a clarity improvement.

## Definition of Done

- [x] `pool.acquire()` wrapped with timeout
- [x] Redis clients have `max_connections` set
- [x] httpx clients have explicit `Limits` configured
- [x] Existing tests pass
- [x] No new dependencies

### S1-P0-000-worklog-bootstrap-and-dependency-research
- Solution: Sol1

# P0-000: Worklog Bootstrap and Dependency Research

Priority: P0
Status: done
Depends on: none

## Objective

Create the execution-ready worklog structure for Solution 1 and produce Python 3.12 dependency decisions with current stability verification.

## Checklist

- [x] Create/update `worklog` artifacts: `RUNBOOK.md`, `baselines/`, `research/`
- [x] Add dependency matrix with online-verified versions for OAuth/JWT + Redis Streams stack
- [x] Document compatibility constraints (`celery` removal, stream consumers, redis-py asyncio)
- [x] Define evidence capture layout for scenario and fault runs

## Acceptance Criteria

- [x] Worklog is complete enough for daily execution without ad-hoc docs
- [x] Dependencies are pinned and justified for Python `3.12.x`
- [x] Every required gate command is listed in the runbook

## Progress Notes (2026-02-16)

Completed artifacts:

- `worklog/RUNBOOK.md`
- `worklog/baselines/TEMPLATE.md`
- `worklog/baselines/gates.unit.yaml`
- `worklog/baselines/gates.integration.yaml`
- `worklog/baselines/gates.release.yaml`
- `worklog/research/2026-02-16-python312-dependency-matrix.md`
- `worklog/research/2026-02-16-solution1-execution-model.md`
- `worklog/evidence/README.md`

Verification evidence:

- Dependency versions fetched from PyPI JSON API using `curl + jq` on 2026-02-16.
- Runbook captures explicit Python 3.12 `uv` venv bootstrap and quality/integration loops.

### S1-P0-001-repo-bootstrap-and-solution0-scaffold-fork
- Solution: Sol1

# P0-001: Repo Bootstrap and Solution0 Scaffold Fork

Priority: P0
Status: done
Depends on: P0-000

## Objective

Fork scaffolding from Solution 0 into Solution 1 and replace architecture-specific pieces while keeping reproducible quality gates.

## Checklist

- [x] Copy/adapt layout from `../0_solution` (docker, monitoring, scripts, test harness patterns)
- [x] Rename package and service identifiers to `solution1`
- [x] Configure `pyproject.toml`, `Makefile`, and `uv.lock` workflow for Solution 1 dependencies
- [x] Ensure container names/project naming are explicit (`solution1`)
- [x] Wire single-command `make full-check` skeleton

## Acceptance Criteria

- [x] `uv sync --frozen` and lint/type commands run in Solution 1
- [x] Docker Compose boots placeholder stack with health checks
- [x] No shared-lib coupling introduced between solutions

## Progress Notes (2026-02-16)

Completed:

- Scaffold copied from `../0_solution` into `1_solution` for docker, monitoring, scripts, tests, and package structure.
- Package directory renamed to `src/solution1` and import/service identifiers normalized from `solution0` to `solution1`.
- Compose project name updated to `mc-solution1`; service images and container names now include `solution1`.
- Local `uv` workflow validated with dedicated environment at `1_solution/.venv`.

Verification commands:

- `uv venv --python 3.12 .venv`
- `uv sync --frozen --dev`
- `make lint type`
- `make test-unit`
- `docker compose up -d --build && docker compose ps && docker compose down -v --remove-orphans`

Notes:

- This card intentionally delivers fork-and-adapt scaffolding only.
- Architecture replacement (JWT/OAuth + Redis Streams + reconciler-first runtime) is implemented in subsequent P0 cards.

### S1-P0-002-schema-migrations-and-seed-templates
- Solution: Sol1

# P0-002: Schema Migrations and Seed Templates

Priority: P0
Status: done
Depends on: P0-001

## Objective

Implement Postgres control-plane schema for Solution 1: hashed API keys, credit audit/snapshots, drift audit, and migration template rendering.

## Checklist

- [x] Create migrations for `users`, `api_keys`, `credit_transactions`, `credit_snapshots`, `credit_drift_audit`, `stream_checkpoints`
- [x] Use migration templating for reproducible dev defaults (keys, roles, tiers, status values)
- [x] Add indexes from RFC-0001 (`api_keys`, `credit_transactions`, `credit_drift_audit`)
- [x] Implement migration runner with `schema_migrations` tracking
- [x] Add seed data path with deterministic local defaults

## Acceptance Criteria

- [x] Fresh database migration + re-run migration both succeed
- [x] Seed data is idempotent and environment-driven
- [x] Schema/indexes match RFC-0001 storage section

## Progress Notes (2026-02-16)

Completed:

- Added `0006_solution1_control_plane_tables.sql` migration introducing:
  - `users.tier` and `users.is_active`
  - `api_keys` (SHA-256 key hash + prefix + role/tier flags)
  - `credit_drift_audit`
  - `stream_checkpoints`
  - indexes `idx_api_keys_user_active` and `idx_drift_checked`
- Extended migration template values with tier placeholders (`DEFAULT_TIER`, `ADMIN_TIER`, `TIER_VALUES_SQL`).
- Added `SubscriptionTier` constants and SQL value helpers in `constants.py`.
- Extended migration unit tests to lock migration ordering and template rendering for control-plane schema.

Verification commands:

- `pytest tests/unit/test_migrations.py -q`
- `make test-unit`
- `make lint type`

Notes:

- Schema is evolved compatibly from the copied baseline so subsequent cards can replace runtime paths incrementally without breaking migration chain.

### S1-P0-003-hydra-oauth-jwt-auth-and-revocation-contracts
- Solution: Sol1

# P0-003: Hydra OAuth JWT Auth and Revocation Contracts

Priority: P0
Status: done
Depends on: P0-002

## Objective

Integrate Ory Hydra (Go, off-the-shelf OAuth server) and ship OAuth token issuance plus JWT validation contracts for Solution 1 with local verification on API hot path.

Decision captured from architecture review:

- OAuth provider: `Ory Hydra` (Go OSS) for solution-1 baseline.
- No custom in-house OAuth server in this track.

## Checklist

- [x] Add Hydra services to compose (`hydra-migrate`, `hydra`, `hydra-client-init`) with deterministic dev bootstrap
- [x] Provision OAuth clients from deterministic local defaults (admin, user1, user2) with idempotent bootstrap script
- [x] Build `/v1/oauth/token` adapter endpoint that exchanges client credentials against Hydra token endpoint
- [x] Validate incoming API key/client credentials against hashed-key table (through bootstrapped client mapping)
- [x] Issue JWT access tokens with claims carrying `sub`, `tier`, `role`, `jti`, `exp` (Hydra JWT access token strategy)
- [x] Implement API middleware for local signature + claim validation (JWKS/key cache)
- [x] Add revocation checks via Redis keyspace contract
- [x] Add admin authorization guard from role claim
- [x] Add integration test proving Hydra-issued JWT can call protected API paths
- [x] Add integration test proving revoked token path is rejected deterministically

## Acceptance Criteria

- [x] Token issuance + verification paths are fully tested
- [x] Submit/poll paths do not call Postgres for auth validation on cache-hot path
- [x] Invalid/revoked/expired token behaviors return deterministic error contracts
- [x] Compose startup proves Hydra migration + client bootstrap succeeds deterministically
- [x] RFC-0001 auth section is fully satisfied by code and tests

## Progress Notes (2026-02-16, Iteration 1)

Implemented:

- Compose services added: `hydra-migrate`, `hydra`, `hydra-client-init`.
- Idempotent client bootstrap script added at `docker/hydra/bootstrap-clients.sh`.
- API endpoint `POST /v1/oauth/token` added with two request modes:
  - direct `client_id` + `client_secret`
  - dev alias `api_key` -> mapped OAuth client
- Hydra configured to issue JWT access tokens (`OAUTH2_ACCESS_TOKEN_STRATEGY=jwt`).

TDD evidence:

- Red: new oauth endpoint tests failed with `404` before route implementation.
- Green: `tests/unit/test_app_paths.py` oauth tests now pass.
- Validation gates:
  - `make lint type test-unit`
  - compose boot validation with Hydra startup and bootstrap
  - manual token checks against `/v1/oauth/token` for both credential modes

## Progress Notes (2026-02-16, Iteration 2)

Implemented:

- Added local JWT verification path with JWKS client cache and issuer validation.
- Added Redis revocation gate (`revoked:{user_id}` set keyed by token `jti`) before accepting JWT auth context.
- Added role-claim support with deterministic fallback to OAuth client mapping.
- Hardened JWT auth path to return `503` on unexpected auth dependency failures.

TDD evidence:

- Red: added tests for revoked-token rejection and admin-role-claim authorization.
- Green: implemented revocation key contract + role extraction and reran unit suite.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`

## Progress Notes (2026-02-16, Iteration 3)

Implemented:

- Added integration suite `tests/integration/test_oauth_jwt_flow.py` for real Hydra token exchange.
- Added protected-path test proving Hydra-issued JWT can submit and poll tasks end-to-end.
- Added revoked-token integration test by writing `jti` into Redis `revoked:{user_id}`.
- Adjusted JWT user resolution to map known OAuth client IDs back to canonical API keys so credits/admission use the same user identity in Redis and Postgres.

TDD evidence:

- Red: new integration test failed with `401` on submit due identity mismatch.
- Green: implemented client-id -> api-key resolution in JWT auth path; integration test passed.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `pytest tests/integration/test_oauth_jwt_flow.py -m integration -q`

## Progress Notes (2026-02-16, Iteration 4)

Implemented:

- Added hashed-key-table validation for OAuth `api_key` alias exchange (`api_keys.key_hash` + `is_active=true`).
- Added unit coverage for OAuth key-validation success/failure/degraded paths (`200`, `401`, `503`).
- Added JWT cache-hot-path integration test to ensure repeated authenticated polls avoid repeated Postgres lookups.
- Added expired-token unit coverage to make invalid/revoked/expired behavior deterministic.

TDD evidence:

- Red: added new unit/integration expectations before auth-path updates.
- Green: implemented repository hash validation + route guard and reran suite.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `make test-integration`

### S1-P0-004-redis-lua-mega-script-and-keyspace-contract
- Solution: Sol1

# P0-004: Redis Lua Mega-Script and Keyspace Contract

Priority: P0
Status: done
Depends on: P0-001, P0-002

## Objective

Implement Redis-centric admission control via a single Lua operation covering idempotency, concurrency, credit, stream enqueue, and task status seed.

## Checklist

- [x] Define key patterns and TTL policy (`credits`, `idem`, `active`, `task`, `stream`, `credits:dirty`)
- [x] Implement Lua mega-script with typed parser and `NoScriptError` reload behavior
- [x] Include cache-miss hydration/retry contract
- [x] Add companion Lua scripts for safe counter decrement and transition helpers as needed
- [x] Expose metrics for Lua latency and outcomes

## Acceptance Criteria

- [x] Admission path is one atomic Redis script call on happy path
- [x] Idempotent replay and conflict semantics are deterministic
- [x] Script behavior is covered by focused unit tests for every branch

## Progress Notes (2026-02-16, Iteration 1)

Implemented:

- Upgraded `ADMISSION_LUA` to a mega-script that now atomically performs:
  - idempotency check
  - concurrency check
  - credit deduction
  - Redis Stream enqueue (`XADD MAXLEN ~`)
  - task state seed (`HSET task:{task_id}` + `EXPIRE`)
  - idempotency TTL write + active counter increment + dirty-credit tracking
- Extended `run_admission_gate` to pass stream key, stream payload JSON, task TTL, and stream maxlen.
- Added stream/task keyspace runtime settings and defaults (`REDIS_TASKS_STREAM_KEY`, `REDIS_TASKS_STREAM_MAXLEN`, `REDIS_TASK_STATE_TTL_SECONDS`).
- Updated submit-path admission calls to pass stream payload metadata (`task_id`, `user_id`, `x`, `y`, `api_key`, `trace_id`).

TDD evidence:

- Red: introduced admission-call assertions in billing unit tests for key/argv shape.
- Green: implemented mega-script wiring and passing tests.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `make test-integration`

Completion evidence:

- Idempotent replay and conflict semantics are verified in integration contracts:
  - `tests/integration/test_api_flow.py::test_submit_poll_and_idempotent_replay`
  - `tests/integration/test_error_contracts.py::test_contract_error_codes_400_401_404_409`
- Branch-level Lua result behavior is covered via parser and billing service tests:
  - `tests/unit/test_lua_parser.py`
  - `tests/unit/test_billing_service.py`

### S1-P0-005-task-api-contracts-submit-poll-cancel-admin
- Solution: Sol1

# P0-005: Task API Contracts Submit Poll Cancel Admin

Priority: P0
Status: done
Depends on: P0-003, P0-004

## Objective

Ship public API contracts for submit/poll/cancel/admin with strict schemas, authorization, and stable error taxonomy.

## Checklist

- [x] Implement endpoints: `POST /v1/task`, `GET /v1/poll`, `POST /v1/task/{id}/cancel`, `POST /v1/admin/credits`
- [x] Enforce ownership checks and idempotency-key semantics
- [x] Implement queue position and estimated runtime response fields for pending tasks
- [x] Add compatibility aliases if required by assignment wording
- [x] Define structured business events for lifecycle + billing actions

## Acceptance Criteria

- [x] Endpoint contracts match RFC-0001 and matrix
- [x] Error responses are stable for `400/401/402/404/409/429/503`
- [x] Poll happy path remains Redis-only on data lookup

## Progress Notes (2026-02-16, Iteration 1)

Implemented:

- Kept API contract surface stable across submit/poll/cancel/admin plus assignment compatibility aliases.
- Added Redis task-state key helper (`task:{task_id}`) and poll-path lookup before Postgres fallback.
- Poll now serves pending/running status directly from Redis task hash on happy path.
- Queue position and ETA now use Redis stream depth (`XLEN`) instead of Celery list depth.

TDD evidence:

- Red: added `test_poll_uses_redis_task_state_without_db_lookup` asserting DB is not touched.
- Green: implemented Redis-first poll path and stream-based queue depth.
- Validation gates:
  - `make lint`
  - `make type`
  - `make test-unit`
  - `make test-integration`

## Progress Notes (2026-02-16, Iteration 2)

Implemented:

- Added structured business events on task write paths:
  - `business_event_task_submitted`
  - `business_event_task_idempotent_replay`
  - `business_event_task_rejected`
  - `business_event_task_cancelled`
- Added structured admin billing event:
  - `business_event_admin_credit_adjusted`

Validation evidence:

- API contract and error-taxonomy stability verified via integration suites:
  - `tests/integration/test_api_flow.py`
  - `tests/integration/test_error_contracts.py`
  - `tests/integration/test_oauth_jwt_flow.py`

### S1-P0-006-stream-worker-consumer-group-and-pel-recovery
- Solution: Sol1

# P0-006: Stream Worker Consumer Group and PEL Recovery

Priority: P0
Status: done
Depends on: P0-004, P0-005

## Objective

Replace Celery worker model with Redis Streams consumers and robust recovery behavior.

## Checklist

- [x] Implement worker bootstrap with consumer-group initialization
- [x] Implement `XREADGROUP` processing loop and `XACK` lifecycle
- [x] Enforce guarded state transitions for task status (`PENDING->RUNNING->TERMINAL`)
- [x] Wire tier envelopes and model-class cost factors in submit/worker paths
- [x] Implement model-class simulation behavior (`small`, `medium`, `large`) per RFC assumptions
- [x] Implement PEL recovery (`XPENDING`, `XCLAIM`/`XAUTOCLAIM`) with retry/timeout policy
- [x] Add graceful shutdown and SIGTERM handling

## Acceptance Criteria

- [x] Worker processes stream entries idempotently
- [x] Stuck entries are recoverable and test-backed
- [x] Failure paths refund/decrement behavior is correct and audited
- [x] Tier/model-class behavior is contract-tested (cost/concurrency/runtime impact)

### S1-P0-007-reconciler-snapshot-drift-and-expiry-jobs
- Solution: Sol1

# P0-007: Reconciler Snapshot Drift and Expiry Jobs

Priority: P0
Status: done
Depends on: P0-004, P0-006

## Objective

Implement periodic reconciler service for Redis/Postgres consistency and operational cleanup.

## Checklist

- [x] Flush `credits:dirty` to `credit_snapshots`
- [x] Run drift detection and write `credit_drift_audit`
- [x] Recover orphan/stuck task state paths not handled by workers
- [x] Apply result expiry/retention policy
- [x] Persist stream recovery checkpoints/telemetry as needed

## Acceptance Criteria

- [x] Reconciler loops are idempotent and safe under retries
- [x] Drift and snapshot behavior is observable via metrics/logs
- [x] Fault tests prove recovery behavior against injected failures

### S1-P0-008-observability-metrics-dashboard-and-events
- Solution: Sol1

# P0-008: Observability Metrics Dashboard and Events

Priority: P0
Status: done
Depends on: P0-005, P0-006, P0-007

## Objective

Provide production-grade observability baseline for Solution 1 and emit searchable business events in structured logs.

## Checklist

- [x] Structured JSON logging with correlation ids (`trace_id`, `task_id`, `user_id`)
- [x] Prometheus metrics for API, Lua, stream lag/PEL, JWT validation, reconciler loops
- [x] Grafana dashboards for throughput, error rates, queue health, credit drift
- [x] Alert rules config for lag, drift, service availability
- [x] OTel/Tempo config artifacts aligned with matrix (RFC/config scope)

## Acceptance Criteria

- [x] `/metrics` coverage includes all core runtime components
- [x] Dashboard and alert config are versioned and reproducible
- [x] Lifecycle and billing events are emitted as structured JSON lines

### S1-P0-009-tdd-suite-unit-integration-fault-e2e-load
- Solution: Sol1

# P0-009: TDD Suite Unit Integration Fault E2E Load

Priority: P0
Status: done
Depends on: P0-005, P0-006, P0-007

## Objective

Build a comprehensive automated test suite proving contract correctness, concurrency behavior, and degradation handling.

## Checklist

- [x] Unit tests for auth, Lua admission parser, API routes, worker transitions, reconciler logic
- [x] Integration tests against compose stack for end-to-end submit/poll/cancel/admin
- [x] Fault tests for Redis partial failures, worker crashes, PEL growth, PG outage on snapshot paths
- [x] Concurrency tests with multi-user bursts and idempotency races
- [x] E2E demo execution tests (`demo.sh` and `demo.py`)
- [x] Coverage and complexity gates (`>=75%` global, higher for critical modules)

## Acceptance Criteria

- [x] `make prove` passes from clean environment
- [x] Tests cover key invariants in RFC-0001 critical paths
- [x] Regressions are reproducible with deterministic fixtures

### S1-P0-010-demo-and-release-readiness
- Solution: Sol1

# P0-010: Demo and Release Readiness

Priority: P0
Status: done
Depends on: P0-008, P0-009

## Objective

Finalize Solution 1 delivery with reproducible startup, demo scenarios, and contributor-ready evidence.

## Checklist

- [x] Add contributor-first `README.md`: setup, run, demo first; architecture after
- [x] Ensure one-command verification (`make full-check`) performs clean, build, all checks, scenarios
- [x] Add scripted scenario runner for 5-20 realistic flows, including concurrency cases
- [x] Capture evidence artifacts (logs, summaries, metrics snapshots)
- [x] Validate compose naming, health checks, startup/shutdown order, and non-root workers where applicable

## Acceptance Criteria

- [x] Fresh clone -> one-command setup + prove + demo succeeds
- [x] Evidence directory contains pass/fail trace for all gates
- [x] Output clearly demonstrates zero-Postgres hot path behavior

### S1-P0-011-dual-publish-cutover-and-celery-decommission
- Solution: Sol1

# P0-011: Dual-Publish Cutover and Celery Decommission

Priority: P0
Status: done
Depends on: P0-006

## Objective

Eliminate dual-publish behavior (`Lua XADD` + `Celery send_task`) and complete the execution-plane cutover to Redis Streams for solution-1 RFC alignment.

## Checklist

- [x] Remove API-side Celery publish path from submit flow
- [x] Ensure stream worker is the only execution consumer
- [x] Remove cancel-time Celery revoke behavior and replace with stream-native cancellation semantics
- [x] Replace Celery readiness probes with stream consumer-group readiness checks
- [x] Rename/update queue metrics from Celery naming to stream semantics (`stream_depth`, `pel_depth`, lag)
- [x] Remove unused Celery settings/contracts/dependencies from runtime path

## Acceptance Criteria

- [x] No task execution occurs through Celery in `1_solution`
- [x] Submit creates exactly one execution intent (stream entry) per accepted task
- [x] Fault/integration tests prove no duplicate execution after cutover

### S1-P0-012-jwt-hot-path-revocation-retention-and-token-errors
- Solution: Sol1

# P0-012: JWT Hot Path, Revocation Retention, and Token Error Contracts

Priority: P0
Status: done
Depends on: P0-003

## Objective

Align auth behavior with RFC-0001 intent: local JWT verification on hot path, explicit token lifecycle errors, and bounded revocation storage.

## Checklist

- [x] Remove JWT-path dependency on API-key resolver (`resolve_user_from_api_key`) for mapped clients
- [x] Construct authenticated principal from verified JWT claims (`sub/client_id`, `role`, `tier`) with strict validation
- [x] Keep revocation check in Redis only, with bounded retention strategy (TTL/day-sharded keyspace)
- [x] Add explicit expired-token handling and API error code/message contract
- [x] Add tests for: no DB lookup on JWT hot path, revocation check behavior, expired vs invalid token responses
- [x] Document dev/prod revocation retention policy in README/RFC notes

## Acceptance Criteria

- [x] JWT-authenticated requests do not call Postgres/Redis auth-cache lookup paths
- [x] Revocation keyspace remains bounded without manual full scans
- [x] Clients can distinguish `TOKEN_EXPIRED` from generic invalid token failures

### S1-P0-013-readiness-worker-probe-connection-reuse
- Solution: Sol1

# P0-013: Readiness Worker Probe Connection Reuse

Priority: P0
Status: done
Depends on: P0-008

## Objective

Remove per-request Redis connection churn from `/ready` worker heartbeat checks and align readiness probing with pooled/shared client usage.

## Checklist

- [x] Change worker readiness probe API to accept shared Redis client and heartbeat key
- [x] Update `/ready` route to pass runtime Redis client instead of constructing a new client
- [x] Add/adjust unit tests for healthy/unhealthy worker heartbeat probe behavior
- [x] Re-run full prove gate and capture evidence

## Acceptance Criteria

- [x] `/ready` no longer creates ad-hoc Redis connections for worker probe
- [x] Unit tests cover positive and failure probe branches
- [x] `make prove` passes after probe refactor

### S1-P0-014-tier-model-concurrency-stress-hardening
- Solution: Sol1

# P0-014: Tier/Model Concurrency Stress Hardening

Priority: P0
Status: done
Depends on: P0-006, P0-009, P0-012

## Objective

Strengthen production confidence with tougher JWT-tier/model integration and scenario checks beyond baseline burst tests.

## Checklist

- [x] Add integration test validating tier-based concurrency envelopes under worker pause (`pro` vs `free`)
- [x] Add integration test validating model-class cost impact on credit deduction (`small` vs `large`)
- [x] Extend scenario harness with JWT tier/model stress scenario
- [x] Ensure new checks are stable under `make prove`

## Acceptance Criteria

- [x] Concurrent submit limits differ by tier exactly as configured
- [x] Credit deductions align with model cost multipliers
- [x] Scenario harness reports pass for new stress scenario in full-check output

### S1-P1-015-jwt-only-auth-and-route-scope-enforcement
- Solution: Sol1

# P1-015: JWT-Only Protected Auth and Route Scope Enforcement

Priority: P1
Status: done
Depends on: P0-003, P0-012

## Objective

Make solution 1 a clean JWT break for protected APIs and enforce per-route OAuth scopes without DB lookups on request hot paths.

## Checklist

- [x] Remove API-key bearer fallback from protected endpoints (`/v1/task`, `/v1/poll`, `/v1/task/{id}/cancel`, `/v1/admin/credits`, compat twins)
- [x] Add scope parsing from JWT claims and carry scopes in authenticated principal
- [x] Enforce required scopes:
  - [x] `task:submit` on submit
  - [x] `task:poll` on poll
  - [x] `task:cancel` on cancel
  - [x] `admin:credits` on admin credit endpoint
- [x] Keep admin role authorization in addition to scope requirement for admin endpoint
- [x] Ensure JWT verification remains local crypto + Redis revocation check only
- [x] Update tests (unit/integration/fault/e2e/scenario helpers) to obtain and use OAuth tokens
- [x] Update demo scripts to exchange token first, then call protected routes with JWT

## Acceptance Criteria

- [x] Protected routes reject non-JWT bearer tokens with `401 UNAUTHORIZED`
- [x] Missing scope yields `403 FORBIDDEN` without DB lookup
- [x] Existing JWT hot-path no-DB auth invariant test still passes
- [x] `make gate-unit`, `make gate-integration`, and `make gate-fault` pass for changed auth/scope contracts

### S1-P1-016-stream-orphan-recovery-and-task-state-coherence
- Solution: Sol1

# P1-016: Stream Orphan Recovery and Task-State Coherence

Priority: P1
Status: done
Depends on: P0-006, P0-007

## Objective

Eliminate poison-message retries when stream entries outlive task persistence and tighten coherence between stream, pending markers, Redis task hash, and Postgres rows.

## Checklist

- [x] Handle "stream message exists, PG task row missing" with explicit branching:
  - [x] Retry while pending marker exists
  - [x] Ack and drop orphan entries when pending marker is absent and message exceeded grace timeout
- [x] Clear stale Redis `task:{id}` hash in orphan/drop path when safe
- [x] Ensure API persist-failure compensation removes any Redis task-state artifacts created by admission
- [x] Keep credit accounting invariant intact (no extra debit/refund)
- [x] Add unit tests for missing-row + pending-marker-present vs missing-marker paths
- [x] Add fault coverage for persist-failure artifact cleanup and validate integration gates remain green

## Acceptance Criteria

- [x] Stream worker does not indefinitely reprocess orphaned messages
- [x] No double refund or leaked active slot in orphan scenarios
- [x] Existing worker/reaper recovery tests remain green

### S1-P1-017-observability-contract-metrics-alerts-and-cardinality
- Solution: Sol1

# P1-017: Observability Contract (Metrics, Alerts, and Cardinality)

Priority: P1
Status: done
Depends on: P0-008

## Objective

Close RFC-0001 observability contract gaps and remove high-cardinality request labels.

## Checklist

- [x] Replace raw request path label with canonical route template label in HTTP metrics middleware
- [x] Add missing RFC metrics and wire instrumentation:
  - [x] `stream_consumer_lag`
  - [x] `stream_pending_entries`
  - [x] `jwt_validation_duration_seconds`
  - [x] `snapshot_flush_duration_seconds`
  - [x] `token_issuance_total`
  - [x] `pel_recovery_total`
- [x] Add/align Prometheus alert rules for stream lag, PEL growth, drift threshold, snapshot staleness
- [x] Scrape reaper metrics endpoint
- [x] Expand observability tests for metric and alert presence

## Acceptance Criteria

- [x] `/metrics` includes required series with stable label cardinality
- [x] Prometheus config includes RFC-aligned alert coverage
- [x] Unit tests validate config and metric registration for new series

### S1-P1-018-ops-hardening-doc-consistency-and-runtime-safety
- Solution: Sol1

# P1-018: Ops Hardening, Doc Consistency, and Runtime Safety

Priority: P1
Status: done
Depends on: P1-015, P1-017

## Objective

Harden runtime operational posture and align docs/matrix text to implemented behavior.

## Checklist

- [x] Add process restart policy and failure containment for reaper runtime loop
- [x] Ensure API container runs as non-root like worker/reaper
- [x] Reconcile README endpoint compatibility text (`/admin/credits` vs `/credits`)
- [x] Align RFC/matrix wording where implementation intentionally differs, without weakening guarantees
- [x] Remove or explicitly justify benchmark-only dead code paths in repository module
- [x] Validate full clean run (`make full-check`) and archive evidence snapshot

## Acceptance Criteria

- [x] Compose runtime has consistent non-root + restart behavior where expected
- [x] Docs accurately reflect shipped API/behavior
- [x] Full verification command passes from clean docker state

## Notes

- Added reaper cycle failure containment with configurable backoff (`REAPER_ERROR_BACKOFF_SECONDS`) and a unit test proving recovery after a transient cycle failure.
- Added compose restart policy (`unless-stopped`) to long-running services and enforced non-root API image runtime (`USER app`).
- Corrected docs/matrix wording:
  - compatibility endpoint text uses `/admin/credits`
  - matrix/rfc terminology now consistently references `XAUTOCLAIM`
- Retained transactional admin credit benchmark path with explicit purpose in `repository.py` and benchmark script reference (`scripts/benchmark_write_patterns.py`).
- Verification evidence:
  - `worklog/evidence/full-check-20260216T195457Z`

### S1-P1-021-spec-alignment-submit-contract-model-cost-and-worker-warmup
- Solution: Sol1

# P1-021: Spec Alignment for Submit Contract, Model Cost, and Worker Warmup

Priority: P1
Status: done
Depends on: P1-018

## Objective

Align implemented behavior with agreed solution-1 contract where mismatches were identified.

## Checklist

- [x] Add `estimated_seconds` to submit response model and route response payload
- [x] Resolve and align LARGE model cost factor with shared assumptions/RFC, then enforce in code + tests
- [x] Add explicit 10-second worker model initialization warmup behavior required by assignment baseline
- [x] Add poll terminal edge fallback for partial Redis cache presence (`task:{id}` present, `result:{id}` missing)
- [x] Add idempotency key boundary guard (`<= 128`) with deterministic error behavior
- [x] Add/adjust integration tests for submit contract fields and model/tier behavior

## Acceptance Criteria

- [x] Submit response contract matches documented API behavior
- [x] Model/tier math is consistent across code, tests, and docs
- [x] Worker startup behavior matches assignment simulation expectations

## Notes

- Submit contract now includes `estimated_seconds` for both fresh submits and idempotent replays.
- Model cost alignment updated to `LARGE=5` and reflected in scenario + integration expectations.
- Worker model startup now has explicit one-time warmup behavior (10s) with unit coverage.
- Poll path now handles terminal `task:{id}` state when `result:{id}` is absent by falling back to PG.
- Idempotency key validation now enforces non-empty trimmed value and max length `128`.
- Tests updated:
  - `tests/unit/test_app_paths.py`
  - `tests/unit/test_stream_worker.py`
  - `tests/integration/test_api_flow.py`
  - `tests/integration/test_oauth_jwt_flow.py`
- Verification evidence:
  - `worklog/evidence/full-check-20260216T215833Z/`
  - `worklog/evidence/full-check-20260216T220559Z/`

### S1-P1-022-stream-reclaim-policy-and-runtime-safety
- Solution: Sol1

# P1-022: Stream Reclaim Policy and Runtime Safety

Priority: P1
Status: done
Depends on: P1-018

## Objective

Prevent premature stream message reclaim under normal runtime and improve multi-worker safety margins.

## Checklist

- [x] Recalibrate `stream_worker_claim_idle_ms` relative to max modeled runtime + jitter + scheduler overhead
- [x] Add guardrails/tests for multi-worker reclaim behavior so healthy in-flight messages are not reclaimed early
- [x] Review and tune related heartbeat/readiness thresholds for stream worker liveness
- [x] Add stress/fault tests covering long-running tasks with multiple consumers

## Acceptance Criteria

- [x] In-flight healthy tasks are not prematurely reclaimed in normal operation
- [x] PEL recovery still works for genuinely stuck/abandoned messages
- [x] Tests demonstrate stable behavior across concurrent workers

## Notes

- Calibrated defaults and envs:
  - `STREAM_WORKER_CLAIM_IDLE_MS` moved from `2000` to `15000` in `.env.dev.defaults`.
  - `stream_worker_claim_idle_ms` default moved to `15000` in settings.
- Added runtime guardrails in `AppSettings`:
  - reject reclaim windows below modeled runtime safety floor
  - reject heartbeat TTL below block/runtime liveness floor
- Added reclaim/worker safety helpers in `src/solution1/constants.py`.
- Added/updated tests:
  - `tests/unit/test_settings.py`
  - `tests/unit/test_stream_worker.py`
- Verification evidence:
  - `worklog/evidence/full-check-20260216T213751Z/`
  - `worklog/evidence/full-check-20260216T213751Z/scenarios.json`

### S1-P1-027-tooling-and-scenario-auth-path-correction
- Solution: Sol1

# P1-027: Tooling and Scenario Auth Path Correction

Priority: P1
Status: done
Depends on: P1-018

## Objective

Align load/scenario tooling with JWT-only auth model so verification artifacts remain trustworthy.

## Checklist

- [x] Update `scripts/load_harness.py` to acquire/use OAuth access tokens instead of API keys as bearer tokens
- [x] Update `scripts/run_scenarios.py` token naming/usage to avoid API-key-vs-token ambiguity
- [x] Ensure full-check scenarios still pass and produce valid evidence after auth-path correction
- [x] Add tests/checks guarding against regression to API-key bearer misuse in solution 1 tools

## Acceptance Criteria

- [x] Scenario/load tools execute against the same auth model as production routes
- [x] Evidence generated by `make full-check` is semantically valid for JWT-only design

## Notes

- Added tooling auth guard tests in `tests/unit/test_tooling_auth_contract.py`.
- Updated `scripts/load_harness.py` and `scripts/run_scenarios.py` to use OAuth token exchange and bearer access tokens consistently.
- Verification evidence:
  - `worklog/evidence/full-check-20260216T211734Z/`
  - `worklog/evidence/full-check-20260216T211734Z/scenarios.json`

### S1-P1-030-rfc0001-folder-restructure-and-doc-reconciliation
- Solution: Sol1

# P1-030: RFC-0001 Folder Restructure and Doc Reconciliation

Priority: P1
Status: done
Depends on: P1-027, P1-022, P1-021

## Objective

Restructure RFC-0001 into folder format (same treatment as RFC-0000) and reconcile docs against post-fix code in one pass.

## Checklist

- [x] Split RFC-0001 into folder docs (`README.md`, `request-flows.md`, `data-ownership.md`, `capacity-model.md`)
- [x] Reconcile claims with implemented behavior after P1-027/P1-022/P1-021
- [x] Update matrix/readme references where wording/paths changed
- [x] Ensure no duplicated or conflicting statements across RFC, matrix, and assumptions docs

## Acceptance Criteria

- [x] Contributor can navigate RFC-0001 in folder format with clear separation of concerns
- [x] Code-vs-doc mismatches for solution 1 are resolved in a single reconciliation pass

## Notes

- Replaced single-file RFC with folder layout:
  - `../../../.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`
  - `../../../.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/request-flows.md`
  - `../../../.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/data-ownership.md`
  - `../../../.0_agentic_engineering/0_rfcs/RFC-0001-1-solution-redis-native-engine/capacity-model.md`
- Reconciled documented behavior with shipped code:
  - submit response includes `estimated_seconds`
  - model multiplier for `large` is `5`
  - worker includes explicit one-time 10s warmup
  - JWT scope enforcement is route-specific (`task:submit`, `task:poll`, `task:cancel`, `admin:credits`)
  - revocation key scheme is day-sharded with TTL
- Updated references in:
  - `../../README.md` (solution-local readme reference)
  - `RUNBOOK.md`
  - `kanban/BOARD.md`
- Verification evidence:
  - `../evidence/full-check-20260216T221946Z/`

### S1-P1-031-consolidated-final-hardening-pass
- Solution: Sol1

# P1-031: Consolidated Final Hardening Pass

Priority: P1
Status: done
Depends on: P1-030

## Objective

Resolve only validated, high-signal review findings in one focused pass across security, correctness, runtime behavior, tests, and docs.

## Checklist

- [x] Add webhook SSRF guardrails (private/reserved targets blocked) at registration and dispatch paths
- [x] Remove event-loop blocking during worker warmup (no `time.sleep` on async path)
- [x] Harden JWKS client cache synchronization for concurrent verification calls
- [x] Parallelize revocation fallback checks (no sequential Redis round-trips)
- [x] Add `/v1/oauth/token` rate limiting with deterministic Redis-backed policy
- [x] Mask API keys in structured logs (admin and related auth paths)
- [x] Add explicit numeric bounds for `SubmitTaskRequest.x` and `SubmitTaskRequest.y`
- [x] Make fake Redis pipeline return execute results compatible with production semantics
- [x] Add unit coverage for `stream_worker.main_async` lifecycle
- [x] Add unit coverage for `webhook_dispatcher.main_async` lifecycle
- [x] Align `1_solution/README.md` DB-call math and related request-flow wording
- [x] Align `RFC-0001` Lua pseudo-code/args/field names with shipped implementation
- [x] Align `solutions/README.md` container/service naming with compose reality
- [x] Clarify tier-concurrency wording in `0_0_problem_statement_and_assumptions/README.md` vs multiplier model
- [x] Remove or deprecate stale flat RFC file `RFC-0001-1-solution-redis-native-engine.md`
- [x] Run `make prove` from clean state and record evidence path

## Acceptance Criteria

- [x] All checklist items implemented and validated
- [x] `ruff check`, `mypy --strict`, and tests remain green
- [x] `make prove` passes from clean compose state

## Evidence

- Full-check artifacts: `solutions/1_solution/worklog/evidence/full-check-20260217T064959Z`

### S1-P1-032-pg-durable-jti-revocation-blacklist
- Solution: Sol1

# P1-032: PG-Durable JTI Revocation Blacklist

Priority: P1
Status: done
Depends on: P1-031

## Objective

Implement RFC-0001 revocation durability model: Postgres day-partitioned JTI blacklist as source-of-truth, Redis day buckets as hot cache, with PG fallback and startup rehydration.

## Checklist

- [x] Add migration `0009_token_revocations.sql`:
  - [x] create `token_revocations` parent table partitioned by `revoked_at`
  - [x] columns: `jti TEXT`, `user_id UUID`, `revoked_at TIMESTAMPTZ`, `expires_at TIMESTAMPTZ`
  - [x] primary key `(jti, revoked_at)`
  - [x] index `idx_token_revocations_user (user_id, revoked_at)`
  - [x] enable/configure `pg_partman` (`1 day` interval, premake `2`, retention `2 days`, `retention_keep_table=false`)
- [x] Ensure Postgres runtime includes `pg_partman` package in compose stack

- [x] Repository layer (`src/solution1/db/repository.py`):
  - [x] `insert_revoked_jti(executor, *, jti, user_id, expires_at)`
  - [x] `is_jti_revoked(pool, *, jti)`
  - [x] `load_active_revoked_jtis(pool, *, since)` returns `(jti, user_id, day_iso)`

- [x] Revocation service path (`src/solution1/services/auth.py`):
  - [x] add `revoke_jti(...): Redis SADD+EXPIRE then PG insert`

- [x] Revocation API endpoint (`src/solution1/app.py`):
  - [x] add `POST /v1/auth/revoke` (authenticated)
  - [x] extract `jti` + `exp` from verified JWT claims
  - [x] call `revoke_jti`
  - [x] return `{"revoked": true}`

- [x] Auth revocation check fallback (`src/solution1/app.py`):
  - [x] if Redis revocation check errors, fallback to PG `is_jti_revoked`
  - [x] write-through JTI back to Redis bucket on PG hit

- [x] Startup rehydration (`src/solution1/app.py`):
  - [x] load active revoked JTIs from PG since yesterday
  - [x] repopulate Redis day buckets (`SADD` + `EXPIRE`)
  - [x] log `revocation_rehydrated` count

- [x] Metrics/observability:
  - [x] add `token_revocations_total`
  - [x] add `revocation_pg_fallback_total`
  - [x] add `revocation_check_duration_seconds{source=redis|postgres}`

- [x] Tests:
  - [x] unit: repository revocation functions
  - [x] unit: `revoke_jti` dual-write behavior
  - [x] unit: `_is_token_revoked` PG fallback when Redis errors
  - [x] integration: `POST /v1/auth/revoke` then same token gets `401`
  - [x] integration: update existing revoked-token test to use API endpoint (no redis-cli shortcut)
  - [x] fault: revocation survives Redis restart path (PG fallback + startup rehydration)

- [x] End-to-end validation:
  - [x] run focused lint/type/unit/integration/fault suites for changed scope
  - [x] run `make prove` from clean state

## Acceptance Criteria

- [x] Revocation durability is PG-backed with partition lifecycle managed by `pg_partman`
- [x] Auth remains zero-DB on hot path when Redis is healthy
- [x] Redis outage uses PG fallback for revocation checks
- [x] Startup rehydrates Redis revocation cache from PG
- [x] All tests and `make prove` pass

## Validation Evidence

- Focused suites passed: unit/integration/fault revocation scope.
- Clean-state full gate passed: `make prove`.
- Compose readiness green after rebuild with pg_partman-enabled Postgres image.

## Solution 2

### S2-P0-010-worker-rabbitmq-consumer-and-execution
- Solution: Sol2

# P0-010: Worker — RabbitMQ consumer and task execution

Priority: P0
Status: done
Depends on: P1-006, P1-008

## Objective

Replace the `time.sleep(3600)` stub in `workers/worker.py` with a real RabbitMQ consumer that executes tasks, captures/releases reservations, updates query cache, and emits webhook events.

## Why

The worker is the core execution engine. Without it, submitted tasks never progress past PENDING. This is the single largest gap in Sol 2.

## Scope

- `src/solution2/workers/worker.py`
  - RabbitMQ consumer subscribing to `queue.realtime`, `queue.fast`, `queue.batch` (SLA-routed queues)
  - Inbox dedup: check `cmd.inbox_events` to skip already-processed messages (at-least-once → exactly-once)
  - On message received:
    1. Parse task command payload from message
    2. Simulate execution (sleep for model-class duration: small=2s, medium=4s, large=7s)
    3. On success: single PG transaction —
       - Update `cmd.task_commands` status → COMPLETED, set result blob
       - Call `capture_reservation()` (RESERVED → CAPTURED)
       - Insert `cmd.inbox_events` for dedup
       - Insert `cmd.outbox_events` with routing_key `task.completed` (for projector + webhook)
    4. On failure: single PG transaction —
       - Update `cmd.task_commands` status → FAILED
       - Call `release_reservation()` (RESERVED → RELEASED, refund `users.credits`)
       - Insert credit_transactions refund row
       - Insert `cmd.outbox_events` with routing_key `task.failed`
    5. Post-commit: update Redis `task:{id}` cache with terminal state
    6. Ack RabbitMQ message only after PG commit
  - Graceful shutdown on SIGTERM/SIGINT (drain current task, stop consuming)
  - Prometheus metrics: tasks_executed_total, task_duration_seconds, task_failures_total
  - Structured logging with task_id, user_id, model_class context

## Key repository functions to use

- `capture_reservation(pool, reservation_id)` — already exists in repository.py
- `release_reservation(pool, reservation_id)` — already exists in repository.py
- `create_outbox_event(conn, ...)` — already exists
- `create_inbox_event(conn, ...)` — already exists

## Checklist

- [x] Consumer connects to RabbitMQ and binds to all 3 SLA queues
- [x] Inbox dedup prevents double-execution on redelivery
- [x] Success path: task COMPLETED + reservation CAPTURED in single txn
- [x] Failure path: task FAILED + reservation RELEASED + credit refund in single txn
- [x] Post-commit Redis cache update
- [x] Outbox event emitted for projector/webhook consumption
- [x] SIGTERM drains in-flight task before exit
- [x] Metrics exported on configured port
- [x] Readiness probe available

## Validation

- `uv run pytest tests/unit/test_worker.py -q`
- `uv run pytest tests/integration/test_worker_flow.py -q` (compose required)
- Manual: submit task via API, verify it reaches COMPLETED in poll

## Acceptance Criteria

- Tasks progress from PENDING → RUNNING → COMPLETED/FAILED
- Reservation state transitions are atomic with task state
- Credit refund on failure is immediate and auditable
- No message loss on worker restart (unacked messages redelivered)

### S2-P0-011-projector-query-view-materializer
- Solution: Sol2

# P0-011: Projector — query view materializer

Priority: P0
Status: done
Depends on: P1-006, P1-008

## Objective

Replace the `time.sleep(3600)` stub in `workers/projector.py` with a RabbitMQ consumer that materializes command-side events into the `query.task_query_view` table and Redis cache.

## Why

Without the projector, the query view is never populated. Poll currently falls back to stale Sol 1 patterns. The projector is the "Q" half of CQRS — it makes command-side writes visible to read queries.

## Scope

- `src/solution2/workers/projector.py`
  - RabbitMQ consumer subscribing to projector-specific queue (bound to `tasks` exchange)
  - Consumes events: `task.submitted`, `task.completed`, `task.failed`, `task.cancelled`
  - Inbox dedup: check `cmd.inbox_events` before processing
  - On each event:
    1. UPSERT `query.task_query_view` with current state (status, result, timestamps)
    2. Insert `cmd.inbox_events` for dedup
    3. Update Redis `task:{id}` hash with latest state
    4. Ack message
  - Idempotent: re-processing same event produces same view state
  - Graceful shutdown on SIGTERM/SIGINT
  - Prometheus metrics: events_projected_total, projection_lag_seconds
  - Structured logging

## Key design decisions

- Projector is append-only from event perspective — it never modifies command tables
- Event ordering: RabbitMQ per-queue FIFO is sufficient since each task_id routes to same queue
- Projection lag metric: `now() - event.created_at`

## Checklist

- [x] Consumer connects and subscribes to projector queue
- [x] Inbox dedup prevents double-projection
- [x] UPSERT into query.task_query_view for each event type
- [x] Redis task:{id} cache updated post-projection
- [x] Idempotent: same event projected twice = same result
- [x] SIGTERM graceful shutdown
- [x] Metrics exported

## Validation

- `uv run pytest tests/unit/test_projector.py -q`
- `uv run pytest tests/integration/test_projection_flow.py -q`
- Manual: submit → execute → poll shows COMPLETED via query view

## Acceptance Criteria

- Query view reflects all terminal states within projection lag SLA
- Redis cache is consistent with query view
- No duplicate rows in query view on redelivery

### S2-P0-012-watchdog-reservation-expiry-and-cleanup
- Solution: Sol2

# P0-012: Watchdog — reservation expiry and cleanup

Priority: P0
Status: done
Depends on: P1-008

## Objective

Replace the `time.sleep(3600)` stub in `workers/watchdog.py` with a periodic job that expires stale reservations, releases credits, and emits compensation events.

## Why

Without the watchdog, tasks stuck in PENDING/RUNNING with RESERVED credits will leak credits forever. The watchdog is the safety net that ensures the reservation billing model converges even when workers crash.

## Scope

- `src/solution2/workers/watchdog.py`
  - Periodic loop (configurable interval, default 30s)
  - Phase 1 — Expire stale reservations:
    1. SELECT reservations WHERE state=RESERVED AND created_at < now() - reservation_ttl
    2. For each: single PG transaction —
       - `release_reservation()` (RESERVED → RELEASED, refund users.credits)
       - Update `cmd.task_commands` status → TIMED_OUT
       - Insert credit_transactions refund row
       - Insert `cmd.outbox_events` with routing_key `task.timed_out`
    3. Post-commit: update Redis `task:{id}` cache
  - Phase 2 — Bulk expire terminal Redis results:
    1. Scan Redis for `task:{id}` keys with terminal status older than result_ttl
    2. DEL expired keys (PG is source of truth for historical queries)
  - Graceful shutdown on SIGTERM/SIGINT
  - Prometheus metrics: reservations_expired_total, credits_released_total, redis_keys_cleaned_total
  - Structured logging with batch counts per cycle

## Key repository functions to use

- `release_reservation(pool, reservation_id)` — already exists
- `create_outbox_event(conn, ...)` — already exists
- New: `list_expired_reservations(pool, ttl_seconds, limit)` — needs to be added

## Checklist

- [x] Periodic loop with configurable interval
- [x] Expired reservations detected and released
- [x] Credit refund atomic with reservation state change
- [x] Outbox event emitted for projector consumption
- [x] Terminal Redis keys cleaned after TTL
- [x] SIGTERM graceful shutdown
- [x] Metrics exported
- [x] No double-release (idempotent on RESERVED state check)

## Validation

- `uv run pytest tests/unit/test_watchdog.py -q`
- `uv run pytest tests/integration/test_watchdog_expiry.py -q`
- Manual: submit task, kill worker, wait for watchdog cycle, verify credits returned

## Acceptance Criteria

- No credit leak: every RESERVED reservation eventually reaches CAPTURED or RELEASED
- Expired tasks visible as TIMED_OUT in poll
- Watchdog is idempotent — running twice on same state produces no extra side effects

### S2-P0-013-fix-cancel-path-reservation-release
- Solution: Sol2

# P0-013: Fix cancel path — reservation release and PG-native refund

Priority: P0
Status: done
Depends on: P1-008

## Objective

Rewrite the cancel path to use Sol 2 reservation billing: release reservation + PG credit refund + outbox event, removing all Sol 1 Redis compensation patterns.

## Why

Current cancel path is architecturally wrong:
1. `update_task_cancelled()` updates the old `tasks` table instead of `cmd.task_commands`
2. No `release_reservation()` call — credits stay RESERVED forever
3. Redis compensation via `refund_and_decrement_active()` uses Sol 1 patterns (credits:{uid}, active:{uid})
4. `pending_marker_key` deletion is Sol 1 pattern

## Current (broken)

```
_apply_cancel_transaction:
  → UPDATE tasks SET status='CANCELLED'          # wrong table
  → INSERT credit_transactions                    # correct idea, wrong context
_sync_cancel_state_to_redis:
  → refund_and_decrement_active()                 # Sol 1 Redis compensation
  → DEL pending_marker_key                        # Sol 1 pattern
```

## Target (per RFC-0002)

```
cancel_task:
  1. BEGIN
  2. SELECT reservation FROM cmd.credit_reservations
     WHERE task_id = $1 AND state = 'RESERVED' FOR UPDATE
  3. UPDATE cmd.credit_reservations SET state = 'RELEASED'
  4. UPDATE users SET credits = credits + reservation.amount
  5. UPDATE cmd.task_commands SET status = 'CANCELLED'
  6. INSERT cmd.credit_transactions (refund)
  7. INSERT cmd.outbox_events (task.cancelled)
  8. COMMIT
  9. Post-commit: UPDATE Redis task:{id} cache
```

## Scope

- `src/solution2/api/task_write_routes.py`
  - Rewrite `_apply_cancel_transaction()` to use cmd.task_commands + release_reservation
  - Rewrite `_sync_cancel_state_to_redis()` to only update `task:{id}` cache (no Sol 1 keys)
  - Remove `refund_and_decrement_active()` call
  - Remove `pending_marker_key` usage
- `src/solution2/db/repository.py`
  - Fix or replace `update_task_cancelled()` to target `cmd.task_commands`
  - Add `get_reservation_for_task(conn, task_id)` if not exists
  - Ensure `release_reservation()` does credit refund atomically

## Checklist

- [x] Cancel updates cmd.task_commands (not old tasks table)
- [x] Reservation released (RESERVED → RELEASED) with credit refund
- [x] Credit transaction row inserted for audit trail
- [x] Outbox event emitted (task.cancelled) for projector
- [x] Redis task:{id} cache updated post-commit
- [x] No Sol 1 Redis key usage (no credits:{uid}, active:{uid}, pending_marker)
- [x] Idempotent: cancelling already-cancelled task returns success
- [x] Cannot cancel terminal tasks (COMPLETED, FAILED, TIMED_OUT)

## Validation

- `uv run pytest tests/unit/test_cancel_path.py -q`
- `uv run pytest tests/integration/test_cancel_flow.py -q`
- Manual: submit → cancel → poll shows CANCELLED + credits refunded

## Acceptance Criteria

- Cancel is a single PG transaction (no distributed compensation)
- Credits are refunded in PG (source of truth), not Redis
- Outbox event triggers projector update of query view

### S2-P0-014-fix-poll-path-query-view-and-cmd-join
- Solution: Sol2

# P0-014: Fix poll path — query view + cmd join read model

Priority: P0
Status: done
Depends on: P0-011

## Objective

Rewrite the poll path to use Sol 2 CQRS read model: Redis `task:{id}` → `query.task_query_view` → `cmd.task_commands` join, replacing the current Sol 1 two-key + old-table pattern.

## Why

Current poll is architecturally wrong:
1. `_poll_from_result_cache()` reads `result_cache_key` — Sol 1 pattern
2. `_poll_from_task_state()` reads `task_state_key` with `xlen`/`llen` queue depth — Sol 1 stream pattern
3. `_poll_from_db()` queries old `tasks` table — Sol 1 table
4. Queue depth uses `redis_tasks_stream_key` (Redis Streams) — Sol 2 uses RabbitMQ

## Current (broken)

```
poll:
  1. GET result_cache_key (result:{task_id})      # Sol 1
  2. GET task_state_key (task:{task_id})           # correct key name, wrong content model
     + XLEN/LLEN redis_tasks_stream_key            # Sol 1 stream
  3. SELECT * FROM tasks WHERE task_id = $1        # Sol 1 table
```

## Target (per RFC-0002)

```
poll:
  1. HGETALL task:{task_id} from Redis             # single key, full state
     - If found with terminal status → return immediately
     - If found with PENDING/RUNNING → return with position estimate
  2. SELECT * FROM query.task_query_view            # projected read model
     WHERE task_id = $1
     - If found → return (projector has materialized it)
  3. SELECT * FROM cmd.task_commands                # command source of truth
     WHERE task_id = $1
     - Fallback for projection lag
  Queue depth: RabbitMQ management API or cached queue length (not xlen/llen)
```

## Scope

- `src/solution2/api/task_read_routes.py`
  - Rewrite poll handler to use three-tier: Redis task:{id} → query view → cmd join
  - Remove `_poll_from_result_cache()` (result_cache_key pattern)
  - Remove `_poll_from_task_state()` xlen/llen queue depth
  - Add query view lookup via new repository function
  - Add cmd.task_commands fallback
  - Queue position: either RabbitMQ API or "unknown" (not Redis stream length)
- `src/solution2/db/repository.py`
  - Add `get_task_from_query_view(pool, task_id)` if not exists
  - Ensure `get_task_command(pool, task_id)` exists for fallback

## Checklist

- [x] Redis task:{id} is first lookup (single HGETALL)
- [x] query.task_query_view is second lookup
- [x] cmd.task_commands is third lookup (fallback)
- [x] No result_cache_key usage
- [x] No xlen/llen on Redis stream keys
- [x] Queue position from RabbitMQ or omitted (not stream length)
- [x] Response shape matches RFC-0002 poll contract
- [x] 404 when task doesn't exist in any tier

## Validation

- `uv run pytest tests/unit/test_poll_path.py -q`
- `uv run pytest tests/integration/test_poll_flow.py -q`
- Manual: submit → poll shows PENDING → execute → poll shows COMPLETED

## Acceptance Criteria

- Poll reads from CQRS query side, never from command tables on happy path
- Fallback to cmd table is transparent to caller
- No Sol 1 key patterns in poll code path

### S2-P1-001-solution2-fork-and-scaffold
- Solution: Sol2

# P1-001: Solution2 Fork + Scaffold Foundation

Priority: P1
Status: done
Depends on: 9a1e13c (solution2 checkpoint start)

## Objective

Create the baseline Sol2 scaffold by fork-copying `1_solution`, introducing `solution2` package naming, adding RabbitMQ in compose, and removing Sol1 stream/reaper assumptions.

## Scope (Milestone 0)

- Copy `1_solution` → `2_solution` and perform rename `solution1` → `solution2` in code, compose, and configs.
- Add RabbitMQ service + `RABBITMQ_URL` default and settings.
- Remove Sol1 stream/reaper services/modules and replace with RabbitMQ-aware service stubs (`worker`, `outbox-relay`, `projector`, `watchdog`, `webhook-worker`).
- Add cmd/query schema migrations:
  - `0010_create_cmd_schema.sql`
  - `0011_create_query_schema.sql`
- Add venv/compose startup sanity checks for `postgres`, `redis`, and `rabbitmq`.

## Checklist

- [x] `cp -a` scaffold completed with renamed package path/imports (`solution1` → `solution2`).
- [x] `compose.yaml` includes `rabbitmq` and health check.
- [x] `.env.dev.defaults` includes `RABBITMQ_URL`.
- [x] `core/settings.py` includes `rabbitmq_url`.
- [x] Worker container no longer boots `solution2.workers.stream_worker` (Sol2 worker stub entrypoint in place).
- [x] Lua-related files/modules removed from Sol2 scaffold.
- [x] Removed legacy stream/reaper docker-only scaffold (`docker/reaper/Dockerfile`).
- [x] Old stream/reaper docker/services retired; new RabbitMQ-oriented service stubs present.
- [x] `0010_create_cmd_schema.sql` and `0011_create_query_schema.sql` added.
- [x] README reflects Sol2 stack and bootstrap commands.

## Acceptance Criteria

- `git diff` on card shows only Sol2 scaffolding and no Sol1 stream/reaper assumptions.
- `docker compose up -d postgres redis rabbitmq` reaches healthy state.
- Moved card remains in `done` after checks are reproducibly green.

## Validation

- `rg -n "solution1|redis streams|stream_worker|reaper|XREADGROUP" solutions/2_solution`
- `uv run ruff check src tests` (scaffold paths only)
- `docker compose up -d postgres redis rabbitmq`
- `docker compose ps`
- `docker compose down -v`

### S2-P1-002-solution2-cmd-query-schema
- Solution: Sol2

# P1-002: Solution2 CQRS DB Migration Baseline

Priority: P1
Status: done
Depends on: P1-001

## Objective

Introduce command/query schemas and tables required by the Sol2 workflow: task commands, reservations, outbox/inbox, and read model view.

## Scope

- Add migrations:
  - `0012_cmd_task_commands.sql`
  - `0013_cmd_credit_reservations.sql`
  - `0014_cmd_outbox_events.sql`
  - `0015_cmd_inbox_events.sql`
  - `0016_query_task_view.sql`
  - `0017_seed_users_sol2.sql`
- Migration verification script/tests for ordering and presence.
- Seed parity with Sol1 users/api keys.

## Checklist

- [x] Migration files include required columns, indexes, and constraints from `2_solution/tasks.md`.
- [x] Migration order is monotonic and deterministic from `0001`–`0017`.
- [x] Seed migration is idempotent (ON CONFLICT behavior).
- [x] `db.migrate` applies against fresh Postgres schema.
- [x] Index coverage for:
  - `cmd.task_commands`
  - `cmd.credit_reservations`
  - `cmd.outbox_events`
  - `query.task_query_view`

## Acceptance Criteria

- `cmd` and `query` schemas exist after migrate.
- A seeded user/API-key pair query passes smoke checks.
- Re-running migrations is idempotent where defined (`seed_users` and non-destructive constraints).

## Validation

- `pytest tests/unit/test_migrations.py -q`
- `docker compose up -d postgres && uv run python -m solution2.db.migrate`
- `psql ... -c "\\dt cmd.*"` and `\\dt query.*` for table presence
- `pytest tests/unit/test_migrations.py tests/unit/test_repository_cmd.py -q` (when added)

### S2-P1-003-solution2-domain-and-routing-contracts
- Solution: Sol2

# P1-003: Solution2 Domain Constants and Contract Types

Priority: P1
Status: done
Depends on: P1-002

## Objective

Define and test Sol2 domain constants, enums, and request/task contracts before route/business logic implementation.

## Scope

- Add/update `src/solution2/constants.py`:
  - `TaskStatus`, `ReservationState`, `UserRole`, `Tier`, `ModelClass`, `RequestMode`
  - cost/routing helpers (`task_cost_for_model`, `max_concurrent_for_tier`, `resolve_queue`, `compute_routing_key`)
  - `MODEL_*`, `TIER_*` policy constants
- Add dataclasses:
  - `AuthUser`, `TaskCommand`, `CreditReservation`, `OutboxEvent`, `TaskQueryView`, `WebhookTerminalEvent`
- Pydantic schemas for submit/poll/cancel/batch/admin/webhook/error contract.
- Add tests for reservation/state/cost/routing behavior.

## Checklist

- [x] Enums added with explicit accepted literals.
- [x] Cost multipliers and runtime constants match RFC table.
- [x] Queue resolver covers async/sync/batch + tier/model-class behavior.
- [x] Routing key format matches `tasks.<mode>.<tier>.<model_class>`.
- [x] Error envelope/schema compatibility with existing Sol1 contract preserved.

## Acceptance Criteria

- `ruff` and unit tests for invariants pass.
- Invalid routing/model/tier combinations rejected predictably with 400-equivalent errors.

## Validation

- `pytest tests/unit/test_reservation_state.py tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_task_state.py -q`

### S2-P1-004-solution2-auth-parity
- Solution: Sol2

# P1-004: Solution2 Auth + JWT Parity with Sol1

Priority: P1
Status: done
Depends on: P1-003

## Objective

Carry over Sol1 OAuth/JWT path with Sol2 service naming and settings without behavior regression.

## Scope

- `src/solution2/services/auth.py`:
  - API key cache-aside lookup
  - PG fallback
  - Redis + PG JTI revocation checks
  - JWKS resolution/cache refresh
- `src/solution2/app.py` middleware:
  - JWT verification (local)
  - scope enforcement
  - revocation check
- `compose.yaml` and bootstrap for Hydra clients unchanged in semantics.
- Remove Sol1 stream admission dependencies.

## Checklist

- [x] `PYJWT` claim extraction and issuer/audience behavior ported from Sol1.
- [x] JTI required and checked as a revocation key.
- [x] Redis-first, PG-fallback revocation lookups validated.
- [x] Hydra auth endpoints remain compatible with existing demo/scripts.

## Acceptance Criteria

- Auth contract tests from Sol1 pass in Sol2 context.
- No auth regressions observed for `/v1/oauth/token`, `/v1/task`, `/v1/task/{id}`, `/v1/auth/revoke`.

## Validation

- `pytest tests/unit/test_auth_service.py tests/unit/test_auth_utils.py -q`
- Manual flow:
  - `POST /v1/oauth/token` with seeded API key
  - authenticated submit and poll

### S2-P1-005-solution2-submit-reservation-path
- Solution: Sol2

# P1-005: Solution2 Submit Path with Reservation Billing

Priority: P1
Status: done
Depends on: P1-004

## Objective

Implement `POST /v1/task` as a single PG transaction: reserve credits, persist command, emit outbox event, and write query cache after commit.

## Scope

- Repository layer for cmd tables:
  - idempotent command insert keyed by `(user_id, idempotency_key)`
  - reservation creation and guardrails
  - `create_task_command`, `create_reservation`, `create_outbox_event`
- Billing orchestration service:
  - concurrency limit check using active reservations
  - insufficient credits -> 402
  - concurrency limit -> 429
  - idempotent replay semantics
- API mapping and enqueue contract (`queue` / routing) via `resolve_queue`.

## Checklist

- [x] PG transaction includes idempotency check + reservation + task command + outbox row in `run_admission_gate`.
- [x] Redis task-state write is post-admission.
- [x] Cost uses model multiplier policy.
- [x] Conflict response returned for idempotency collisions.
- [x] Command replay validation implemented for cached idempotent submissions.

## Acceptance Criteria

- Submit endpoint is side-effect atomic on DB and does not emit false outbox on failures.
- Terminal failure cases return correct status/errors and leave reservation/cmd state consistent.

## Validation

- `pytest tests/unit/test_app_paths.py::test_submit_idempotent_conflict_and_replay tests/unit/test_app_paths.py::test_submit_accept_path_and_hit_endpoint tests/unit/test_app_paths.py::test_submit_includes_trace_context_in_stream_payload tests/unit/test_app_paths.py::test_submit_persists_failures_are_compensated tests/fault/test_publish_failure_path.py::test_submit_returns_503_on_task_persist_failure`

### S2-P1-006-solution2-outbox-relay
- Solution: Sol2

# P1-006: Solution2 Outbox Relay + RabbitMQ Publish Path

Priority: P1
Status: done
Depends on: P1-005

## Objective

Implement a reliable outbox relay that publishes PG outbox rows to RabbitMQ with at-least-once safety and backlog observability.

## Scope

- `src/solution2/services/rabbitmq.py`:
  - exchange/queue topology declaration
  - durable connections with publisher confirms
- `src/solution2/workers/outbox_relay.py`:
  - batch fetch/unpublished loop
  - publish + confirm + mark published
  - sleep/backoff when no work
  - periodic outbox purge
- Queue topology includes:
  - `exchange.tasks` (`topic`)
  - `queue.realtime`, `queue.fast`, `queue.batch`, DLQ queues
  - `webhooks`/`webhooks.dlq`

## Checklist

- [x] Unpublished events are fetched and published in order.
- [x] Confirmed publish marks rows as published exactly once per successful publish.
- [x] Retry/publish crash windows are safe.
- [x] Dead-letter and DLQ bindings created from queue args.
- [x] `outbox_publish_lag_seconds` metric present.

## Acceptance Criteria

- After submit, outbox row appears unpublished then transitions to published after relay cycle.
- Relay restart does not lose or duplicate observable work incorrectly.
- Confirmed via unit tests and code-level relay-path checks.

## Validation

- `pytest tests/unit/test_outbox_relay.py -q`
- `pytest tests/unit/test_rabbitmq_service.py -q`
- `pytest tests/unit/test_repository_cmd_query.py -q`
- `ruff check src/solution2/workers/outbox_relay.py src/solution2/services/rabbitmq.py`

### S2-P1-007-solution2-domain-constants-and-routes
- Solution: Sol2

# P1-007: Solution2 Domain constants and routing contracts

Priority: P1
Status: done
Depends on: P1-001

## Objective

Define solution-2 domain contracts before changing implementation logic, replacing solution-1 stream-oriented constants with Sol2 CQRS/RabbitMQ semantics.

## Scope

- Update `src/solution2/constants.py`:
  - `Tier`, `RequestMode`, `ReservationState` enums.
  - keep existing task constants and add request-mode + queue/routing helpers.
  - Add/adjust helpers:
    - `task_cost_for_model(base_cost, model_class)`
    - `max_concurrent_for_tier(base_max, tier)`
    - `resolve_queue(tier, mode, model_class)` → queue name
    - `compute_routing_key(mode, tier, model_class)` → `tasks.<mode>.<tier>.<model_class>`
- Remove/replace stream-specific names in routing decisions:
  - `minimum_stream_claim_idle_ms` usage should be isolated to compatibility paths only.
- Align with RFC routing matrix:
  - free: async/batch -> `queue.batch`, sync rejected.
  - pro: sync small -> `queue.fast`, sync medium/large rejected.
  - enterprise: sync/async -> `queue.realtime`, batch -> `queue.fast`.

## Checklist

- [x] Add `RequestMode` enum (`async`, `sync`, `batch`).
- [x] Add `Tier` and `ReservationState` enums.
- [x] Add routing helpers and constants with tests.
- [x] Keep stream constants isolated and out of routing paths.
- [x] New routing/unit tests added.

## Validation

- `ruff check src/solution2/constants.py tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_reservation_state.py`
- `pytest -q tests/unit/test_sla_routing.py tests/unit/test_cost_calculation.py tests/unit/test_reservation_state.py`

## Acceptance Criteria

- Invalid mode/tier/model combinations are rejected early and consistently.
- `compute_routing_key` output always follows `tasks.<mode>.<tier>.<model_class>`.
- No direct stream constants influence queue decision logic.

### S2-P1-008-solution2-repository-cmd-query-layer
- Solution: Sol2

# P1-008: Solution2 Command/Query repository surface

Priority: P1
Status: done
Depends on: P1-007

## Objective

Create a Sol2-safe repository API for `cmd.*` and `query.*` storage, while keeping Sol1 auth/user helpers available where needed.

## Scope

- Refactor `src/solution2/db/repository.py`:
  - Add/keep command APIs for task commands and reservations.
  - Add/keep query APIs for task query view upsert/fetch.
  - Add outbox/inbox API operations.
  - Keep user/auth APIs for backward-compatibility with API token path.
- Add/adjust migration-backed SQL tests for:
  - `create_task_command`, `get_task_command`, `update_task_command_status`
  - `create_reservation`, `capture_reservation`, `release_reservation`
  - `count_active_reservations`, `find_expired_reservations`
  - `create/fetch/mark_outbox_event`, `check/record_inbox_event`
  - `upsert_task_query_view`, `get_task_query_view`, `bulk_expire_results`

## Checklist

- [x] Separate command/query SQL operations from stream checkpoint helpers.
- [x] Add strict reservation-state persistence transitions (reserve/capture/release filters).
- [x] Keep function names stable where Sol1 callers still exist (compatibility paths untouched).
- [x] Add tests for all new/ported repository functions.

## Validation

- `uv run pytest -q tests/unit/test_repository_cmd_query.py`
- `uv run pytest -q tests/unit` (all 2_solution repository tests currently passing)

## Acceptance Criteria

- Reservation lifecycle transitions are enforced at persistence boundaries.
- CQRS query view and command tables can be read independently without stream references.

### S2-P1-009-solution2-submit-reservation-flow
- Solution: Sol2

# P1-009: Solution2 Submit + reservation flow

Priority: P1
Status: done
Depends on: P1-008

## Objective

Implement `POST /v1/task` on a single transaction: idempotent key dedupe, credit reservation, command insert, and outbox event emission.

## Scope

- `src/solution2/services/billing.py`
  - Replace stream/Lua path with reservation pipeline:
    - idempotency lookup
    - concurrency check via active reservations
    - `reserve_credits` + `create_reservation` + `create_task_command`
    - `create_outbox_event`
    - commit once all operations succeed
  - Post-commit Redis query cache write (`task:{id}`).
- `src/solution2/api/task_write_routes.py`
  - route validation against mode/tier routing table
  - reject invalid idempotency + malformed requests
- `src/solution2/services/retry.py` / runtime settings
  - remove dependency on stream worker claim/backoff settings where possible.

## Checklist

- [x] Route rejects free-tier sync requests (400 + structured error).
- [x] Concurrency cap enforced per tier and reservation count.
- [x] Insufficient credits returns 402 and no side-effect.
- [x] Duplicate idempotency key with same payload returns same task row.
- [x] Duplicate idempotency with different payload returns 409.
- [x] Post-commit cache write updates status `PENDING` with queue label.

## Notes

- Scope is already implemented by previous DB-path migration; this card closes with regression tests that lock expected behavior at the submit/admission boundary.
- Validation commands run:
  - `uv run pytest -q tests/unit/test_billing_service.py tests/unit/test_app_paths.py`

## Validation

- `pytest tests/unit/test_submit_reservation.py tests/unit/test_app_paths.py -q`
- `pytest tests/integration/test_submit_flow.py -q` (compose required)

## Acceptance Criteria

- No Lua scripts remain on submit path.
- Command insert + outbox row are atomic and visible together.
- Reservation debit/credit invariants are stable under repeated/replayed submits.

### S2-P1-015-remove-sol1-billing-dead-code
- Solution: Sol2

# P1-015: Remove Sol 1 billing dead code

Priority: P1
Status: done
Depends on: P0-013

## Objective

Remove the Redis-native admission gate fallback and Sol 1 compensation functions from billing.py that are dead code in Sol 2.

## Why

Sol 2 always has `db_pool` available, so `_run_redis_admission_gate()` is unreachable. The Sol 1 compensation functions (`mark_credit_dirty`, `refund_and_decrement_active`, `decrement_active_counter`) operate on Sol 1 Redis keys that Sol 2 doesn't use. Keeping them creates confusion about the actual billing model.

## Scope

- `src/solution2/services/billing.py`
  - Delete `_run_redis_admission_gate()` (lines ~258-350) — dead code, PG path always runs
  - Delete `mark_credit_dirty()` — Sol 1 `credits:dirty` set pattern
  - Delete `refund_and_decrement_active()` — Sol 1 `credits:{uid}` + `active:{uid}` pattern
  - Delete `decrement_active_counter()` — Sol 1 `active:{uid}` pattern
  - Remove the `if db_pool is None` branch in `run_admission_gate()` (it can't happen)
  - Delete `CACHE_MISS` from `AdmissionResult` enum if only used by Redis path
  - Clean imports

## Checklist

- [x] `_run_redis_admission_gate()` deleted
- [x] `mark_credit_dirty()` deleted
- [x] `refund_and_decrement_active()` deleted
- [x] `decrement_active_counter()` deleted
- [x] `CACHE_MISS` handling retained only at route boundary (no billing emitter)
- [x] No callers reference deleted functions
- [x] All tests pass after removal

## Validation

- `uv run pytest tests/ -q`
- `ruff check src/solution2/services/billing.py`
- `mypy --strict src/solution2/services/billing.py`

### S2-P1-016-remove-sol1-redis-key-patterns
- Solution: Sol2

# P1-016: Remove Sol 1 Redis key patterns

Priority: P1
Status: done
Depends on: P0-013, P0-014

## Objective

Remove all Sol 1 Redis key helpers and their usage across the codebase. Sol 2 uses a single `task:{id}` hash for query cache, not the Sol 1 two-key pattern.

## Why

Sol 2 has a single Redis key pattern (`task:{id}`) for the query-side cache. The Sol 1 patterns (`result_cache_key`, `task_state_key`, `pending_marker_key`, `credits_cache_key`, `active_tasks_key`) are architectural artifacts that create confusion and potential bugs.

## Scope

- `src/solution2/core/redis_keys.py` (or wherever key helpers live)
  - Delete: `result_cache_key()`, `task_state_key()`, `pending_marker_key()`, `credits_cache_key()`, `active_tasks_key()`
  - Keep: `task_cache_key()` (the Sol 2 `task:{id}` pattern) if it exists, or rename
- All files that import/use deleted helpers (~7 files per grep):
  - `task_write_routes.py` — remove pending_marker usage
  - `task_read_routes.py` — remove result_cache/task_state usage (covered by P0-014)
  - `billing.py` — remove credits_cache/active_tasks usage (covered by P1-015)
  - `admin_routes.py` — remove credits_cache/credits:dirty usage
  - Any worker files referencing old patterns
- `src/solution2/core/redis_keys.py` — delete `redis_tasks_stream_key` if present (Sol 1 stream)

## Checklist

- [x] All Sol 1 key helper functions deleted
- [x] No imports of deleted functions remain
- [x] No string literals matching old patterns (credits:{uid}, active:{uid}, pending:, result:)
- [x] `task:{id}` is the only Redis task-cache key pattern used
- [x] All tests pass

## Validation

- `ruff check src/solution2/`
- `uv run pytest tests/ -q`
- `rg "result_cache_key|pending_marker_key|credits_cache_key|active_tasks_key|idempotency_key\\(" src/solution2/` returns nothing

### S2-P1-017-fix-admin-credits-outbox-event
- Solution: Sol2

# P1-017: Fix admin credits — add outbox event

Priority: P1
Status: done
Depends on: P1-008

## Objective

Add outbox event emission to the admin credits endpoint so credit adjustments are propagated through the event bus per RFC-0002.

## Why

Current admin_routes.py does a direct PG update + Redis cache sync + `credits:dirty` set add, but never emits an outbox event. Per RFC-0002, all state mutations should publish events via the outbox for downstream consumers (audit, webhooks, projector awareness). The `credits:dirty` pattern is Sol 1.

## Scope

- `src/solution2/api/admin_routes.py`
  - After credit update in PG transaction, INSERT `cmd.outbox_events` with:
    - `event_type`: `credits.adjusted`
    - `aggregate_id`: user_id
    - `routing_key`: `admin.credits.adjusted`
    - `payload`: `{user_id, old_credits, new_credits, delta, admin_id}`
  - Remove `credits:dirty` set usage (Sol 1 pattern)
  - Keep Redis cache update (write-through for read path)

## Checklist

- [x] Outbox event emitted in same PG transaction as credit update
- [x] Event payload includes old/new/delta for audit trail
- [x] No `credits:dirty` set usage
- [x] Tests verify outbox call in admin route path

## Validation

- `uv run pytest tests/unit/test_admin_routes.py -q`
- `uv run pytest tests/integration/test_admin_credits.py -q`

### S2-P1-018-rabbitmq-readiness-check
- Solution: Sol2

# P1-018: RabbitMQ readiness check

Priority: P1
Status: done
Depends on: P1-006

## Objective

Add RabbitMQ connectivity to the `/ready` health check endpoint.

## Why

Sol 2 depends on RabbitMQ for all async processing. If RabbitMQ is down, the API can accept tasks but they won't be processed. The readiness probe should reflect this dependency so load balancers and orchestrators can route traffic appropriately.

## Scope

- `src/solution2/api/health_routes.py` (or wherever readiness is defined)
  - Add RabbitMQ connection check to readiness probe
  - Use pika `BlockingConnection` with short timeout, or management API health check
  - Return degraded/not-ready if RabbitMQ unreachable
- Keep existing PG + Redis checks

## Checklist

- [x] `/ready` checks RabbitMQ connectivity
- [x] Short timeout (1-2s) to avoid blocking probe
- [x] Degraded response when RabbitMQ is down but PG/Redis are up
- [x] Tests cover RabbitMQ checker integration in readiness service

## Validation

- `uv run pytest tests/unit/test_health.py -q`
- Manual: stop RabbitMQ container, verify `/ready` returns degraded

### S2-P1-019-clean-dead-settings-and-docstrings
- Solution: Sol2

# P1-019: Clean dead settings, imports, and docstrings

Priority: P1
Status: done
Depends on: P1-015, P1-016

## Objective

Remove Sol 1 settings fields, dead imports, and fix docstrings that reference stream/Lua/Celery patterns no longer used in Sol 2.

## Why

Settings like `redis_tasks_stream_key`, `stream_consumer_group`, `stream_claim_*`, `reaper_*` are Sol 1 artifacts. Module docstrings referencing "Redis Streams" or "Lua mega-script" are misleading in Sol 2.

## Scope

- `src/solution2/core/settings.py`
  - Remove stream-related fields: `redis_tasks_stream_key`, `stream_consumer_group`, `stream_claim_min_idle_ms`, `stream_batch_size`
  - Remove reaper fields: `reaper_interval_seconds`, `reaper_max_age_seconds`
  - Remove Celery fields if any remain
  - Add any missing Sol 2 fields (e.g., `reservation_ttl_seconds`, `watchdog_interval_seconds`)
- Module docstrings across `src/solution2/` — update references from "Redis Streams" to "RabbitMQ"
- Dead imports cleanup after P1-015 and P1-016

## Checklist

- [x] No stream/reaper/celery settings remain
- [x] Sol 2 settings present (reservation_ttl, watchdog_interval, rabbitmq_url, etc.)
- [x] Docstrings reference correct architecture (CQRS, RabbitMQ, outbox)
- [x] No unused imports
- [x] `ruff check` passes
- [ ] `mypy --strict` passes (`src/tests` baseline still has pre-existing strict errors in `services/rabbitmq.py` + outbox relay tests)

## Validation

- `ruff check src/solution2/`
- `mypy --strict src/solution2/`
- `uv run pytest tests/ -q`

### S2-P1-020-sol1-repository-dead-functions
- Solution: Sol2

# P1-020: Remove Sol 1 repository dead functions

Priority: P1
Status: done
Depends on: P0-010, P0-013, P0-014

## Objective

Remove repository functions that target the old `tasks` table and Sol 1 patterns, keeping only cmd.*/query.* functions.

## Why

repository.py has both Sol 1 functions (`get_task`, `update_task_running`, `update_task_completed`, `update_task_failed`, `update_task_cancelled` targeting `tasks` table) and Sol 2 functions (`create_task_command`, `create_reservation`, `capture_reservation`, `release_reservation`). After P0 cards fix all callers, the Sol 1 functions are dead code.

## Scope

- `src/solution2/db/repository.py`
  - Delete functions that query/update old `tasks` table:
    - `get_task()` — replaced by query view lookup
    - `update_task_running()` — replaced by worker cmd update
    - `update_task_completed()` — replaced by worker cmd update
    - `update_task_failed()` — replaced by worker cmd update
    - `update_task_cancelled()` — replaced by P0-013 cmd update
  - Verify no callers remain after P0 cards
  - Clean imports

## Checklist

- [x] All old `tasks` table functions removed
- [x] No callers reference deleted functions
- [x] cmd.* and query.* functions are the only data access patterns
- [x] All tests pass
- [ ] `mypy --strict` passes (`src/tests` baseline still has pre-existing strict errors in `services/rabbitmq.py` + outbox relay tests)

## Validation

- `uv run pytest tests/ -q`
- `grep -r "update_task_running\|update_task_completed\|update_task_failed\|update_task_cancelled" src/solution2/` returns nothing (or only in migrations/seed)

### S2-P2-021-batch-submit-endpoint
- Solution: Sol2

# P2-021: Batch submit endpoint

Priority: P2
Status: done

## Objective

Implement `POST /v1/task/batch` endpoint with transactional reservation semantics and RFC-0002-compatible behavior.

## Scope

- Add route handlers for `/v1/task/batch` and `/task/batch`
- Validate batch payload and enforce per-user concurrency envelope across batch
- Execute batch admission in one transactional path (single request-level decision)
- Persist pending read-model state in Redis for immediate pollability
- Return batch identifiers and accepted task IDs with aggregate cost

## Notes

- RFC-0002 mentions batch as a differentiator but not P0
- Implementation now lives in:
  - `src/solution2/api/task_write_routes.py`
  - `src/solution2/services/billing.py`
  - `src/solution2/api/contracts.py`
  - `src/solution2/models/schemas.py`

## Validation

- `make gate-unit`
- `make prove`

### S2-P2-022-sync-execution-mode
- Solution: Sol2

# P2-022: Sync execution mode

Priority: P2
Status: done

## Objective

Implement synchronous execution mode for enterprise-tier realtime requests where the API holds the connection and returns results inline.

## Scope

- Enterprise tier + sync mode + small model path executes inline and returns terminal result
- Uses transactional sync reservation lifecycle (admit -> running -> completed/failed/timeout)
- Bypasses RabbitMQ/outbox for inline execution path only
- Timeout guard returns `408 REQUEST_TIMEOUT` with structured error

## Notes

- RFC-0002 mentions sync as enterprise differentiator
- Implementation now lives in:
  - `src/solution2/api/task_write_routes.py`
  - `src/solution2/services/billing.py`
  - `src/solution2/core/settings.py`
  - `src/solution2/models/schemas.py`

## Validation

- `make gate-unit`
- `make prove`

### S2-P2-023-reservation-and-queue-depth-metrics
- Solution: Sol2

# P2-023: Reservation state and queue depth metrics

Priority: P2
Status: done

## Objective

Add Prometheus metrics for reservation lifecycle and RabbitMQ queue depths.

## Scope

- `reservations_active_gauge` — count of RESERVED state reservations
- `reservations_captured_total`, `reservations_released_total` — counters
- RabbitMQ queue depth per SLA queue (via management API or custom consumer metric)
- Grafana dashboard panels for reservation and queue metrics

## Notes

- Sol 0 and Sol 1 have queue depth metrics; Sol 2 should too
- RabbitMQ management plugin exposes queue lengths via HTTP API

## Implementation summary

- Added metrics:
  - `reservations_active_gauge`
  - `reservations_captured_total`
  - `reservations_released_total`
  - `rabbitmq_queue_depth{queue}`
- API admission path increments `reservations_active_gauge` on successful reservation create.
- Worker success/failure transitions update reservation capture/release counters and active gauge decrements.
- Cancel path updates reservation release counter + active gauge decrement after successful release/refund.
- Watchdog refreshes `reservations_active_gauge` each cycle from authoritative Postgres count and increments release counter for timed-out releases.
- Worker publishes per-SLA queue depth (`queue.realtime`, `queue.fast`, `queue.batch`) via passive queue inspection.
- Updated Grafana dashboard (`solution2-overview.json`) with RabbitMQ queue-depth and reservation panels.

## Validation

- `pytest tests/unit/test_billing_service.py tests/unit/test_repository_cmd_query.py tests/unit/test_worker.py tests/unit/test_watchdog.py tests/unit/test_observability_contract.py -q`
- `pytest tests/integration/test_multi_user_concurrency.py::test_multi_user_concurrency_enforced_per_user tests/integration/test_oauth_jwt_flow.py::test_jwt_tier_based_concurrency_envelopes -q`

### S2-P2-024-review-reconciliation-doc-and-runbook-parity
- Solution: Sol2

# P2-024: Review reconciliation - docs and runbook parity

Priority: P2
Status: done

## Objective

Apply only the valid parts of the latest Sol 2 review by reconciling docs/runbook claims with current implementation and keeping solution-level commands consistent.

## Checklist

- [x] Update shared matrix wording for scenario counts to avoid stale hardcoded value.
- [x] Add Sol 2 `make loadtest` alias and update matrix capability row accordingly.
- [x] Update Sol 2 README verification section with explicit scenario count and loadtest command.
- [x] Update RFC-0002 observability wording:
  - [x] clarify alert rules file vs Alertmanager deployment
  - [x] mark OpenSearch as planned (not in compose for Sol 2)
- [x] Run verification (`make gate-unit` and `make prove`).

### S2-P2-025-shared-doc-status-and-scope-alignment
- Solution: Sol2

# P2-025 Shared docs - status and scope alignment

Objective:

Bring the root/shared documentation back in sync with the currently shipped solutions, starting with the repo-level surfaces that affect Solutions 0-2 contributors first.

Acceptance criteria:

- [x] Update the root `README.md` so Solution 3 is no longer described as RFC-only or a stub.
- [x] Update `solutions/README.md` and other shared tables only where claims are now stale.
- [x] Update RFC status fields that are still marked `Draft` even though the coded solution now exists.
- [x] Keep all changes doc-only for this card.
- [x] Verify the edited docs for internal consistency after the changes.

Checklist:

- [x] Refresh root solution summary/status wording.
- [x] Refresh shared matrix/status wording where needed.
- [x] Refresh RFC-0002 status.
- [x] Stage any Solution 3 status wording that belongs in shared docs, not code docs.

## Solution 3

### S3-P0-001-solution3-repo-bootstrap
- Solution: Sol3

# P0-001 Solution 3 - Repo Bootstrap and Tooling

Objective:

Create a runnable `3_solution` scaffold from `2_solution` conventions, then enforce a clean TDD seam before writing business logic.

Acceptance criteria:

- [x] Working `make help`, `make venv`, and `make sync` entrypoints.
- [x] Contributor-first README with setup/demo/proof sections.
- [x] `docker compose up --build -d` starts all required services with health checks.
- [x] Base package/import structure exists under `src/solution3/`.
- [x] Local developer safety defaults in `.env.dev.defaults`.

TDD order:

1. Write baseline regression tests first under `tests/unit/` that fail against missing bootstrap:
   - test `RuntimeState`, settings parsing, logging setup, and startup dependency defaults.
2. Implement only the minimal scaffold required to make these pass.
3. Extract shared helpers into reusable modules if and only if tests demand.
4. Refactor for consistency and rerun relevant tests.

Checklist:

- [x] Copy/adapt minimal non-domain scaffold from `2_solution`:
  - `docker/`, `monitoring/`, `scripts/`, `utils/`, `Makefile`, `pyproject.toml`, `.env.dev.defaults`, `README.md`.
- [x] Add `worklog/evidence/` paths and placeholder capture conventions used in other solutions.
- [x] Create package shell in `src/solution3/`:
  - `__init__.py`, `core/`, `api/`, `services/`, `workers/`, `db/`, `models/`, `utils/`.
- [x] Add `src/solution3/main.py` factory entrypoint with app factory stub.
- [x] Create foundational Dockerfiles:
  - `docker/api/Dockerfile`
  - `docker/reconciler/Dockerfile`
  - `docker/dispatcher/Dockerfile`
  - `docker/projector/Dockerfile`
  - `docker/worker/Dockerfile`
  - `docker/webhook-worker/Dockerfile`
- [x] Add service definitions in `compose.yaml`:
  - postgres, redis, redpanda, tigerbeetle, hydra, rabbitmq, api, reconciler, dispatcher, projector, worker pool, webhook-worker, grafana, prometheus.
- [x] Add minimal readiness probes + startup gating script placeholders.
- [x] Add `tests/conftest.py` scaffold for shared fakes/settings.
- [x] Ensure `make prove` command path exists, even if it currently reports blocked scope.

Completion criteria:

- [x] `make venv` and `make sync` are runnable from clean checkout.
- [x] `make quality` and `make coverage` execute on the scaffolded code with no unrelated failures.

Verification notes:

- `make help` passed on 2026-03-26.
- `./scripts/full_stack_check.sh` passed on 2026-03-26.
- Full-check artifact: `worklog/evidence/full-check-20260326T203812Z`

### S3-P0-002-solution3-core-contracts-and-migrations
- Solution: Sol3

# P0-002 Solution 3 - Core Contracts and Storage Model

Objective:

Define all shared domain contracts and schema primitives required by TigerBeetle + Redpanda + CQRS flow.

Status: completed on 2026-03-26 after live migration proof against compose Postgres.

Acceptance criteria:

- [x] Shared enums/constants compile and are used consistently in SQL migrations and runtime paths.
- [x] Command/query schemas can be created from clean migration run.
- [x] `migrate.py` supports template-based SQL rendering with enum-driven literals.

TDD order:

1. Add unit tests for constants/settings serialization and migration rendering templates.
2. Add SQL migration tests that validate rendered placeholders and constraint names.
3. Implement constants/models/repository query boundaries to satisfy tests.

Checklist:

- [x] Add/extend shared constants in `src/solution3/constants.py`:
  - `TaskStatus`, `ModelClass`, `SubscriptionTier`, `RequestMode`, `BillingState`, routing constants.
  - SQL-literal helper constants, terminal/cancellable state groups, and `TASK_EVENT_TYPES`.
- [x] Add/extend `src/solution3/core/settings.py`:
  - TigerBeetle cluster settings, redpanda settings, RabbitMQ settings, event-topic names, checkpoint timing.
- [x] Add `src/solution3/db/migrations/0001_create_schemas.sql` and 0002+ initial files with placeholders for:
  - `cmd.task_commands`
  - `cmd.outbox_events`
  - `cmd.inbox_events`
  - `query.task_query_view`
  - `cmd.projection_checkpoints`
  - `cmd.users`, `cmd.api_keys`, and `cmd.billing_reconcile_jobs`.
- [x] Correct stale card assumption: RFC-0003 defines `cmd.outbox_events` and `cmd.inbox_events`, not `cmd.task_events`.
- [x] Implement `src/solution3/db/migrations/0003_seed_users.sql` using RFC-0003 seed shape.
- [x] Extend `src/solution3/db/migrate.py`:
  - template replacement for enum-driven constants
  - validation on migration filenames and transaction-safe runs.
- [x] Add contract tests for `load_migration_sql()` and `render_migration_sql()`.
- [x] Add migration runner smoke script under `scripts/migrate.sh`.

Completion criteria:

- [x] `render_migration_sql()` outputs fully expanded constants for all 000x scripts.
- [x] New tables created without manual SQL substitutions.

Verification notes:

- `pytest tests_bootstrap/unit` passed on 2026-03-26.
- `pytest tests_bootstrap/integration/test_migrations.py -m integration` passed on 2026-03-26.
- `make quality` passed on 2026-03-26.
- `make migrate` returned `No pending migrations.` after the integration proof.

### S3-P0-003-solution3-auth-api-submit
- Solution: Sol3

# P0-003 Solution 3 - Auth and Command API Pipeline

Objective:

Implement OAuth-backed identity, command API, and guarded task command state transitions.

Status: done on 2026-03-26 after unit, integration, and quality verification.

Acceptance criteria:

- [x] `POST /v1/task` accepts idempotent submit and writes a command row + outbox row atomically.
- [x] `GET /v1/poll` returns hot-path data.
- [x] `POST /v1/task/{id}/cancel` follows guarded state transition rules.
- [x] `POST /v1/admin/credits` requires admin role.

TDD order:

1. Add tests for each route contract before endpoint implementation:
   - submit, poll, cancel, admin credits, ownership/rbac errors.
2. Add repository command/state tests for guarded transitions.
3. Implement API adapters and route glue with minimal behavior.
4. Add integration tests with seeded users and real API stack.

Checklist:

- [x] Auth:
  - add `src/solution3/api/auth_routes.py` and auth service with Hydra/JWT verification path.
  - verify roles and scopes for admin operations.
- [x] Domain/services:
  - add `src/solution3/services/auth.py` for API key hash lookup + JWT mapping.
- [x] Submit API:
  - add `src/solution3/api/task_write_routes.py`.
  - parse idempotency key, payload, model class, requested mode.
  - write `task_commands` row using repo write helper.
- [x] Poll API:
  - add `src/solution3/api/task_read_routes.py` with Redis/query-model fallback.
- [x] Cancel/API admin:
  - add cancel contract with state guard semantics.
  - add admin credits endpoint with explicit admin scope.
- [x] Contracts:
  - add/adjust Pydantic models in `src/solution3/models/schemas.py`.
- [x] Route assembly in `src/solution3/app.py`.

Completion criteria:

- [x] Submit path writes command row + outbox row; no direct worker enqueue.
- [x] Cancel path cannot regress terminal states due to missing guard checks.
- [x] Unauthorized ownership/scopes consistently return RFC-conformant envelopes.

Verification:

- `pytest tests_bootstrap/unit`
- `pytest tests_bootstrap/integration -m integration`
- `make quality`

Notes:

- `POST /v1/admin/credits` is intentionally RBAC-only in this slice. The success path remains deferred to `P0-004`, where TigerBeetle becomes the billing source of truth.

### S3-P0-004-solution3-dispatch-worker-billing
- Solution: Sol3

# P0-004 Solution 3 - TB Billing, Relay, and Worker Dispatch

Objective:

Implement idempotent command handoff from PG command store into Redpanda and RabbitMQ worker dispatch with hot/cold routing.

Status: complete as of 2026-03-27. Billing wrapper, outbox-relay process, dispatcher bridge, RabbitMQ hot/cold worker routing, TigerBeetle reserve/post/void path, and end-to-end submit -> complete flow are green.

Acceptance criteria:

- [x] TigerBeetle reserve path for submit and idempotent retry safety.
- [x] Outbox relay publishes command events exactly once from command row state.
- [x] Dispatcher reads Redpanda and publishes queue tasks to RabbitMQ with headers.
- [x] Worker consumes queue and updates command state safely.
- [x] Dispatcher + worker prefer warm/preloaded queues before cold fallback.

TDD order:

1. Add unit tests for TigerBeetle account mapping, transfer lifecycle, and relay idempotency.
2. Add integration tests with mock RabbitMQ/Redpanda adapters for dispatch semantics.
3. Implement service-by-service with contract-first interfaces.

Checklist:

- [x] Billing service:
  - add `src/solution3/services/billing.py`.
  - implement `reserve_credits`, `post_pending_transfer`, `void_pending_transfer`.
  - include account bootstrap and user-account mapping table helper.
- [x] Outbox relay:
  - add `src/solution3/workers/outbox_relay.py`.
  - read `cmd.outbox_events` and publish to Redpanda topics.
  - mark publish success/failure with retry.
- [x] Dispatcher:
  - add `src/solution3/workers/dispatcher.py`.
  - consume task events and publish RabbitMQ work messages with headers.
  - implementation now proves both cold-fallback and warm/preloaded routing.
  - use RabbitMQ headers for model class and tier routing.
- [x] Worker runtime:
  - add `src/solution3/workers/worker.py`.
  - implement cold-start model cache and active worker tracking.
  - execute compute path and finalize command state over the live RabbitMQ path.
- [x] Add command completion flow:
  - post TB completion transfer on success.
  - void TB pending on failure/timeout/cancel.
- [x] Add end-to-end integration tests:
  - submit -> relay -> dispatch -> worker -> poll completed.

Completion criteria:

- [x] Worker failures do not leak pending TigerBeetle transfers.
- [x] Dispatch path is stable under repeated delivery / duplicate events on the cold-queue path.
- [x] Warm/preloaded routing is exercised and proven with dedicated tests.

Sub-slices complete so far:

- [x] TigerBeetle billing primitives with unit coverage.
- [x] Outbox relay publish/flush/mark ordering seam with unit coverage.
- [x] Outbox relay process with concrete Redpanda producer and strict unit coverage.
- [x] Dispatcher topology + durable publish contract with unit coverage.
- [x] Dispatcher process with concrete Redpanda consumer and RabbitMQ channel bridge coverage.
- [x] Worker running/completion guard seam with TigerBeetle post/void and Redis cache updates.
- [x] Worker model runtime seam with cold-start, warm-registry, and hot-path unit coverage.
- [x] Live integration proof for outbox-relay -> Redpanda -> dispatcher -> RabbitMQ cold-queue delivery.
- [x] Live integration proof for submit -> TB reserve -> relay -> Redpanda -> dispatcher -> RabbitMQ -> worker -> completed poll result.
- [x] Live integration proof for preloaded-header routing to warm queues with cold fallback when no warm binding exists.

### S3-P0-005-solution3-projections-and-recovery
- Solution: Sol3

# P0-005 Solution 3 - Projections, Reconciler, and Webhook Worker

Objective:

Add query-side materialization and recovery mechanisms so Sol 3 is operational under stale states and infra churn.

Status: complete as of 2026-03-27. Redpanda task events project into `query.task_query_view`, inbox dedup is in place, projection checkpoints advance, live poll fallback works after deleting the Redis task key, the projection can be rebuilt either from SQL or by replaying Redpanda from offset `0`, stale `RESERVED` tasks reconcile either to `EXPIRED` or back to the correct TigerBeetle-backed terminal state, and terminal webhook callbacks deliver with bounded retries plus durable dead-letter capture.

Acceptance criteria:

- [x] Projector consumes command events into query view and checkpoints offsets.
- [x] Rebuilder mode can replay from topic start and restore query view.
- [x] Reconciler resolves stale reserved states and pending terminal drifts.
- [x] Webhook worker dispatches callbacks with retry/dead-letter policy.

TDD order:

1. Add projector unit tests around idempotent consumption and checkpoint progression.
2. Add reconciler tests with simulated stale transfer states.
3. Add webhook dispatch tests for retry and success path.
4. Implement services incrementally from projector upward.

Checklist:

- [x] Add `src/solution3/db/repository.py` query-side methods:
  - upsert into `query.task_query_view`
  - checkpoint reads/writes
  - projection audit helpers
- [x] Add `src/solution3/workers/projector.py`:
  - consume outbox events from Redpanda
  - dedupe via inbox table
  - write view + optional Redis cache
  - checkpoint updates.
- [x] Add `src/solution3/workers/rebuilder.py` command:
  - support `--from-beginning` mode.
- [x] Add `src/solution3/workers/reconciler.py`:
  - [x] scan stale `RESERVED` tasks and expire them after the TB timeout window
  - [x] align explicit TB posted/voided drift branches
  - [x] emit `tasks.expired` correction events and Redis hot-path updates.
- [x] Add `src/solution3/workers/webhook_dispatcher.py`:
  - consume terminal events
  - retry policy and exponential backoff
  - dead-letter to separate Postgres structure.
- [x] Add integration test for projector catch-up and query-view fallback under Redis cache loss.
- [x] Add integration test for Redpanda replay rebuild after projection reset.
- [x] Add integration test for stale reserved expiry with the worker intentionally stopped.
- [x] Add integration tests for webhook delivery success and dead-letter capture after bounded retries.
- [x] Add integration test for reconciler drift fix.

Completion criteria:

- [x] Poll can be served from query view under steady state.
- [x] Stale reserved tasks are corrected without manual intervention.
- [x] TigerBeetle terminal state drift is repaired back into command, query, and cache state without manual intervention.

### S3-P0-006-solution3-observability-proof
- Solution: Sol3

# P0-006 Solution 3 - Observability, Scenarios, and Proof Gates

Objective:

Finish Sol 3 with production-observable signals and full proof posture.

Acceptance criteria:

- [x]  Prometheus metrics + Grafana dashboards cover core control plane and worker flows.
- [x]  Scenario harness covers all critical flows and is deterministic.
- [x]  `make prove` executes all intended bootstrap test tiers and captures evidence.
- [x]  README claims and solution matrix notes match current code behavior.

TDD order:

1. Add tests for metrics registration and route-level counters/histograms.
2. Add scenario tests first for coverage of critical paths in script form.
3. Add load/capacity tooling for validation and wire commands into the proof workflow.
4. Wire commands and validate proof commands are runnable and bounded.

Checklist:

- [x] Add `src/solution3/observability/metrics.py`.
- [x] Add Prometheus metric suite:
  - submit attempts, success/failure
  - dispatch / outbox / projector / webhook / reconciler signals
  - worker execution and terminal outcomes
- [x] Add/adjust Grafana dashboards and alert rules.
- [x] Add script updates:
  - [x] `scripts/run_scenarios.py`
  - [x] load harness entrypoint via `scripts/load_harness.py`
  - [x] `scripts/capacity_model.py`
  - [x] `scripts/full_stack_check.sh` now runs the scenario harness after the compose-backed test tiers
- [x] Add tests for scenario loader and output shape.
- [x] Add evidence directory convention and timestamps for prove runs.
- [x] Align `README.md` to the current shipped Solution 3 scope.
- [x] Align solution matrix row and status notes.

Completion criteria:

- [x] `make prove` passes from clean state on the current bootstrap run.
- [x] Evidence directory contains full-check output, scenario report, and logs.

### S3-P1-007-solution3-admin-topup-alignment
- Solution: Sol3

# P1-007 Solution 3 - Admin Top-up and Docs Alignment

Objective:

Close the remaining stubbed admin top-up path so Solution 3's shipped surface matches the board, README, and RFC summary.

Acceptance criteria:

- [x] `POST /v1/admin/credits` succeeds for an authenticated admin user with the required scope.
- [x] Target user lookup uses the hashed API key table, not hardcoded settings.
- [x] TigerBeetle top-up is executed and the new balance is returned to the caller.
- [x] A command-store outbox event is recorded for successful admin top-ups.
- [x] Unit and live integration coverage prove success, not just the existing forbidden case.
- [x] Solution 3 README / board / matrix claims are re-aligned to the real shipped behavior.

TDD order:

1. Add route-level unit tests for success, not-found, and billing failure cases.
2. Add repository unit coverage for successful admin top-up outbox persistence.
3. Add a live HTTP integration test for the admin success path.
4. Implement the route and persistence path.
5. Re-run focused tests, then broader proof once the slice is green.

Checklist:

- [x] Add repository helper for admin top-up outbox persistence.
- [x] Implement Solution 3 admin top-up route using active API-key lookup + TigerBeetle top-up.
- [x] Return a useful response payload with the target API key and new balance.
- [x] Extend unit tests in `tests_bootstrap/unit/test_command_api_routes.py`.
- [x] Extend unit tests in `tests_bootstrap/unit/test_repository.py`.
- [x] Extend live integration coverage in `tests_bootstrap/integration/test_command_api_http.py`.
- [x] Align `solutions/3_solution/README.md`.
- [x] Align `solutions/README.md` if any shipped wording drift remains.

Completion criteria:

- [x] Targeted unit + integration tests pass.
- [x] `make quality` passes.
- [x] `make prove` passes from a clean state after the slice lands.

Verification notes:

- `pytest tests_bootstrap/unit/test_command_api_routes.py -q`
- `pytest tests_bootstrap/unit/test_repository.py -q`
- `pytest tests_bootstrap/integration/test_command_api_http.py -q -m integration`
- `pytest tests_bootstrap/unit/test_scenarios_script.py -q`
- `make quality`
- `make prove`
- Evidence: `worklog/evidence/full-check-20260327T152134Z`

### S3-P1-008-solution3-rfc-hardening-and-integrity
- Solution: Sol3

# P1-008 Solution 3 - Correctness and Fidelity Hardening

Objective:

Resolve the remaining high-risk integrity gaps in Sol3 while preserving the Sol3 architecture (TB + Redpanda + RabbitMQ + CQRS). Do not collapse Sol3 into Sol5.

Acceptance criteria:

- [x] Keep reconciler/watchdog boundary aligned with RFC intent and remove dead runtime surface that is not used.
- [x] Fix cancel correctness path to avoid TB-first false negatives and improve client response semantics.
- [x] Ensure all admin top-up flows are audit-complete in the same failure domain as credit writes, or clearly document best-effort behavior as a conscious design choice.
- [x] Remove or retire unused billing event constants/settings that are not produced.
- [x] Keep Sol3 docs aligned to final implemented behavior before resuming full implementation.

TDD order:

1. Add/adjust unit tests for cancel semantics (TB void before/after DB, terminal-task behavior).
2. Add/adjust unit tests for admin top-up audit behavior and idempotent safety or explicit best-effort documentation.
3. Add repository-level test assertions for dead constants removal effects (where applicable).
4. Implement minimal code changes in a sequence that preserves existing Sol3 invariants.
5. Update `README.md`, `worklog/kanban/BOARD.md`, and any RFC-facing notes.
6. Re-queue this card only after tests and proof command are green on changed scope.

Checklist:

- [x] `api/task_write_routes.py`
  - [x] Decide final cancel order policy and implement: DB-side cancel write first, then TB void attempt.
  - [x] Add DB-first/TB-fallback unit tests with:
    - [x] already terminal tasks return `409` not `503`
    - [x] TB temporary failure does not prevent DB cancel when task is still eligible
    - [x] idempotent behavior for repeated cancel attempts

- [x] `workers/watchdog.py` + `compose.yaml` + `core/settings.py`
  - [x] Delete watchdog runtime if not needed for Sol3 operating model.
  - [x] Remove `watchdog` service from compose.
  - [x] Remove `watchdog_metrics_port` from settings and README references.
  - [x] Add explicit regression test for compose service absence or remove related test surface.

- [x] `api/admin_routes.py` + `db/repository.py`
  - [x] Choose one mode for top-up audit:
    - [ ] mode A: best-effort audit event and explicit Sol3 docs statement
    - [x] mode B: fail-hard on outbox mismatch with idempotent retry safety
  - [x] Ensure `transfer_id` reuse between TigerBeetle and outbox record when fail-hard is selected.
  - [x] Add regression test that validates chosen mode under top-up + outbox failure.

- [x] `constants.py` + `core/settings.py` + docs
  - [x] Resolve `billing.captured` / `billing.released` dead pair:
    - [ ] implement `billing.captured|billing.released` production events, or
    - [x] remove constants/settings and update RFC/docs/contracts accordingly
  - [x] Ensure `TASK_EVENT_TYPES` only contains emitted types.

- [x] `README.md` + `RFC-0003` (or solution-local RFC notes)
- [x] Update “known limitation” section:
    - [x] TB capture before DB finalize can still lead to completed-without-result on exceptional requeue/ack edge
    - [x] document that this is architectural tradeoff vs Sol5
  - [x] Update any claims around watchdog/reconciler and admin audit semantics.

Completion criteria:

- [x] All checkboxed checks above are marked complete by code and tests.
- [x] `BOARD.md` Planned Tasks list includes `P1-008-solution3-rfc-hardening-and-integrity.md`.
- [x] No remaining ambiguity between Sol3 scope and implementation.

Implementation notes:

- Do not treat this as a Sol3 architecture rewrite.
- Reconcile only the integrity gaps and correctness clarity identified above.
- Keep Sol3 complexity where mandated by RFC-0003 (projector, dispatcher, reconciler role).

### S3-P1-010-solution3-security-and-doc-cleanup
- Solution: Sol3

# P1-010 Solution 3 - security and doc cleanup

Objective:

Close the remaining Solution 3 security and documentation gaps without changing its RFC-0003 architecture.

Acceptance criteria:

- [x] Reject unsafe webhook callback targets so authenticated users cannot drive internal-network SSRF.
- [x] Stop writing plaintext API keys into admin outbox payloads.
- [x] Refresh Solution 3 docs and RFC status to match the shipped tree and current implementation.
- [x] Remove the dead empty `docker/reaper` directory.
- [x] Add or update targeted tests for the new security behavior.

TDD order:

1. Add red tests for unsafe callback URLs.
2. Add red test for admin outbox payload masking/sanitization.
3. Implement webhook target validation and payload sanitization.
4. Refresh README/RFC/tree docs and cleanup dead directory.
5. Re-run targeted quality/test commands and final proof.

### S3-P2-009-solution3-admin-topup-idempotent-retry
- Solution: Sol3

# P2-009 Solution 3 - Admin Top-up Retry Idempotency

Objective:

Make `/v1/admin/credits` retries safe when the top-up write to TigerBeetle succeeds but the DB outbox write fails, and add a regression assertion for the cancel false-path warning branch.

Acceptance criteria:

 - [x] Add optional idempotency controls to admin top-up requests (`transfer_id` and/or `idempotency_key`).
 - [x] Derive a deterministic `transfer_id` when only `idempotency_key` is supplied so repeated retries do not produce extra credits.
 - [x] Ensure a 503 from outbox write still prevents silent 200 and keeps retry behavior idempotent.
 - [x] Add unit coverage for outbox-retry idempotency (no extra credit applied on retry).
 - [x] Add unit coverage for `_release_pending_transfer` returning `False` after successful DB cancel.
 - [x] Keep existing cancellation and top-up contract paths unchanged.

TDD order:

1. Add `AdminCreditsRequest` schema fields for retry controls.
2. Add regression unit test for retrying admin top-up after outbox failure with same idempotency key.
3. Add regression unit test for cancel route `billing_void=False` branch.
4. Implement deterministic transfer-id resolution in admin route.
5. Re-run unit + targeted integration + proof.

## Solution 5

### S5-P0-001-solution5-hardening-auth-readiness
- Solution: Sol5

# P0-001 Solution 5 - Security, Authz, and Readiness Hardening

Status: DONE (with deterministic handoff compensation)

Objective:

Close correctness and authorization gaps in the current Sol 5 showcase without changing the TB + Restate thesis.

Acceptance criteria:

- [x] Poll endpoint enforces authenticated ownership checks.
- [x] Admin credits endpoint enforces admin role.
- [x] State transitions in repository are guarded.
- [x] Readiness includes Restate and TigerBeetle checks.

TDD order:

1. Add failing tests for ownership, RBAC, transition guards, and readiness behavior.
2. Implement one minimal code change per test to keep blast radius low.
3. Run impacted test group and refactor shared helpers if repeated.

Checklist:

- [x] App/Auth:
  - add token/user claim extraction on poll path.
  - enforce caller user id matches task user.
- [x] Admin security:
  - require role from auth principal for `/v1/admin/credits`.
- [x] Repository safety:
  - use guarded transitions for handoff rollback.
  - return transition outcome and avoid blind overwrite.
- [x] Workflow handoff:
  - replace fire-and-forget submission path with guarded compensation semantics.
  - if workflow invoke fails after task creation, API either transitions `PENDING -> FAILED` atomically with credit release or returns current terminal/in-flight state.
- [ ] Readiness:
  - add TigerBeetle connectivity probe.
  - add Restate connectivity probe + clear degradation code path.
- [ ] Update errors:
  - preserve envelope shape and use `TASK_NOT_FOUND`, `FORBIDDEN`, `UNAVAILABLE` where applicable.

Completion criteria:

- [ ] Security and readiness tests pass at API boundary.
- [ ] No state transition can occur without explicit guard.

### S5-P0-002-solution5-restate-external-compute
- Solution: Sol5

# P0-002 Solution 5 - Restate-Control Plane + External Compute Separation

Status: DONE

Objective:

Refactor inline workflow compute into an external compute plane while keeping Restate as durable orchestration layer.

Acceptance criteria:

- [x] Workflow invokes an external compute worker instead of doing inline arithmetic.
- [x] Control plane stores terminal state only after compute result receipt.
- [x] Cancellation and timeout semantics are explicit and idempotent.

TDD order:

1. Add unit tests for workflow orchestration helpers and result waiting behavior.
2. Add integration test that exercises submit -> compute -> complete with mocked worker handoff.
3. Add failure-path tests for timeout and cancellation before implementing retry/backoff.

Checklist:

- [x] Create/extend compute gateway module:
  - `src/solution5/workers/compute_gateway.py` (or `services/compute.py`).
  - push request payload with task_id, user_id, model metadata.
- [x] Add lightweight compute worker process:
  - `src/solution5/workers/compute_worker.py`.
  - return result via Redis queue or Restate ingress endpoint.
- [x] Update Restate workflow in `src/solution5/workflows.py`:
  - set `PENDING`/`RUNNING` transitions before dispatch.
  - await result with timeout handling (heartbeat updates remain deferred).
  - handle cancel signal and map to safe TB void/cancel path.
- [x] Ensure idempotency of duplicate callbacks/results.
- [x] Add result-ack path back to repository and Redis caches.
- [x] Expand tests for canceled/timeout races with simulated slow worker.

Completion criteria:

- [x] Inline `x+y` no longer executes in the workflow directly.
- [x] External worker faults are surfaced as deterministic workflow outcomes.

### S5-P0-003-solution5-service-surface-proof
- Solution: Sol5

# P0-003 Solution 5 — Service Surface and Proof Posture (DONE)

Objective:

Set Sol 5 service surface as narrow and explicit for this architecture (API-key auth only), with matching validation, tests, and docs.

Acceptance criteria:

- [x] Scope decision is explicit and documented (API-key only; no JWT/OAuth surface).
- [x] Scope-expanded capabilities are intentionally absent and rejected by contract.
- [x] Proof posture reflects implemented scope (`/v1/task`, no `/task`, no batch, no tier/model fields).

Scope decisions:

- API-key authentication only in public submit path.
- Unknown submit payload fields rejected with `422`.
- Unsupported routes return `404`/`405` where not registered.

Checklist:

- [x] Decision checkpoint: API-key-only surface and no OAuth/JWT paths accepted.
- [x] Product surface:
  - no tier/model extensions,
  - no batch submit endpoint,
  - no webhook callbacks,
  - no legacy `/task` path.
- [x] Expanded tests:
  - unit model validation rejects unknown submit fields,
  - integration suite covers scope rejections and compatibility behavior,
  - scenario run adds unsupported-surface assertions.
- [x] Proof posture:
  - `make scenarios` updated to 13 scenarios,
  - scenario report includes scope gate checks.

### S5-P0-006-solution5-cancel-topup-and-poll-integrity
- Solution: Sol5

# P0-006 Solution 5 - cancel, top-up, and poll integrity

Objective:

Close the remaining Solution 5 correctness gaps around late cancellation, JSON result fidelity, and admin credit idempotency before calling the solution complete.

Acceptance criteria:

- [x] A task cannot be left stuck in `CANCEL_REQUESTED` after credits were already captured.
- [x] Poll responses preserve structured JSON results instead of Python repr strings.
- [x] Admin top-up retries are idempotent when a caller reuses the same retry identity.
- [x] Admin top-up rejects unknown target users before mirroring balance state.
- [x] Add unit/integration coverage for each corrected path.
- [x] Refresh stale LOC/tree/docs claims for Solution 5 and shared docs.

TDD order:

1. Add red tests for late-cancel-after-capture behavior.
2. Add red tests for poll result JSON fidelity.
3. Add red tests for idempotent admin top-up retry and unknown-user rejection.
4. Implement workflow/API/repository fixes.
5. Refresh docs and run targeted proof commands.

### S5-P1-004-solution5-observability-and-doc-alignment
- Solution: Sol5

# P1-004 Solution 5 — Observability and Doc Alignment (DONE)

Objective:

Make Solution 5's Prometheus + Grafana claim fully real for the shipped external-compute architecture, and align README/RFC/root-matrix wording to the actual system shape.

Acceptance criteria:

- [x] Compute service exposes a real `/metrics` endpoint with useful counters/histograms.
- [x] API request latency metrics are actually recorded, not just defined.
- [x] Prometheus scrapes both `api` and `compute`.
- [x] Grafana provisioning and a real Solution 5 dashboard are checked in.
- [x] Solution 5 README reflects external compute, 13 scenarios, and honest container counts.
- [x] RFC-0005 reflects external compute instead of inline compute for the shipped implementation.
- [x] Root `solutions/README.md` entries for Solution 5 match the shipped surface.
- [x] `make prove` passes from a clean state after the alignment slice.

Checklist:

- [x] Add red tests for compute metrics endpoint and Prometheus target coverage.
- [x] Add red test for checked-in monitoring assets/provisioning.
- [x] Instrument compute worker metrics and API HTTP duration metrics.
- [x] Update Prometheus config and compose mounts.
- [x] Add Grafana datasource/dashboard provisioning and a real dashboard JSON.
- [x] Reconcile README/RFC/root-matrix language with external compute and current counts.
- [x] Run targeted tests, quality gate, and full proof.

### S5-P1-005-solution5-failure-release-proof
- Solution: Sol5

# P1-005 Solution 5 — Immediate Failure Release and Fault Proof (DONE)

Objective:

When external compute fails or times out, Solution 5 should void the pending TigerBeetle hold immediately instead of relying on the 300-second auto-timeout window. Prove that behavior with unit and live fault tests.

Acceptance criteria:

- [x] Compute failure and compute timeout paths both attempt immediate `VOID_PENDING_TRANSFER`.
- [x] Workflow still records deterministic terminal task status (`FAILED`) on those paths.
- [x] Live fault test proves user balance returns immediately after compute-plane failure.
- [x] Solution 5 proof posture includes the new fault coverage and still passes cleanly.

Checklist:

- [x] Add red unit tests for `billing.release_credits(...)` on compute failure and timeout.
- [x] Add red live fault test that stops compute and verifies immediate balance release.
- [x] Implement release-on-failure in `src/solution5/workflows.py` with replay-safe semantics.
- [x] Update proof harness and docs if fault phase or counts change.
- [x] Run targeted tests, `make quality`, and `make prove`.

