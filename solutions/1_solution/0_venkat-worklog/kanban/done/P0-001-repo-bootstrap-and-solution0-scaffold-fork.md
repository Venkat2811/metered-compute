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
