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
