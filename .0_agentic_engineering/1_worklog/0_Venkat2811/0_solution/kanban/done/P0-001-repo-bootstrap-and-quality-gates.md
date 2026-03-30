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
