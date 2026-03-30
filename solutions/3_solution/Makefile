SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

REPO_ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
VENV_ACTIVATE := source "$(REPO_ROOT).venv/bin/activate"
LOG_DIR ?= $(REPO_ROOT)worklog/evidence/latest

.PHONY: help venv sync migrate rebuild-query replay-query fmt lint type security deps-audit secrets docker-lint docker-lock smell quality \
		test-unit test-integration test-e2e test-fault coverage \
		up down logs ps wait-ready capture-logs demo demo-python scenarios loadtest capacity-model prove full-check

help:
	@echo "Targets:"
	@echo "  venv              Create Python 3.12 virtual environment"
	@echo "  sync              Sync dependencies with uv"
	@echo "  migrate           Apply Solution 3 SQL migrations to local compose Postgres"
	@echo "  rebuild-query     Rebuild query projection from Postgres command tables"
	@echo "  replay-query      Rebuild query projection by replaying Redpanda from offset 0"
	@echo "  fmt               Check formatting with ruff"
	@echo "  lint              Run ruff lint checks"
	@echo "  type              Run mypy strict type checks"
	@echo "  security          Run bandit static security checks"
	@echo "  deps-audit        Run pip-audit dependency vulnerability check"
	@echo "  secrets           Run detect-secrets baseline checks"
	@echo "  docker-lint       Run hadolint on Dockerfiles"
	@echo "  docker-lock       Verify Dockerfiles use uv lock install flow"
	@echo "  smell             Run radon maintainability gate"
	@echo "  quality           Run full quality gate"
	@echo "  test-unit         Run bootstrap unit tests"
	@echo "  test-integration  Run bootstrap integration tests"
	@echo "  test-e2e          Run bootstrap e2e tests"
	@echo "  test-fault        Run bootstrap fault tests"
	@echo "  coverage          Run coverage gate"
	@echo "  up                Start compose stack"
	@echo "  down              Stop compose stack"
	@echo "  logs              Tail compose logs"
	@echo "  ps                Show compose services"
	@echo "  wait-ready        Block until /ready reports ready=true"
	@echo "  capture-logs      Capture compose + metrics logs to LOG_DIR"
	@echo "  demo              Run shell demo smoke"
	@echo "  demo-python       Run Python demo smoke"
	@echo "  scenarios         Run deterministic scenarios"
	@echo "  loadtest          Run the Solution 3 load harness"
	@echo "  capacity-model    Generate capacity projections from the latest load report"
	@echo "  prove             Bootstrap full verification"
	@echo "  full-check        Alias for prove"

venv:
	uv venv --python 3.12 .venv

sync:
	$(VENV_ACTIVATE) && uv sync --dev

migrate:
	@./scripts/migrate.sh

rebuild-query:
	docker compose run --rm --no-deps --build api python -m solution3.workers.rebuilder

replay-query:
	docker compose run --rm --no-deps --build api python -m solution3.workers.rebuilder --from-beginning

fmt:
	$(VENV_ACTIVATE) && ruff format --check src tests_bootstrap

lint:
	$(VENV_ACTIVATE) && ruff check src tests_bootstrap

type:
	$(VENV_ACTIVATE) && mypy --strict src tests_bootstrap

security:
	$(VENV_ACTIVATE) && bandit -q -c pyproject.toml -r src -x tests_bootstrap

deps-audit:
	$(VENV_ACTIVATE) && pip-audit --ignore-vuln GHSA-5239-wwwm-4pmq

secrets:
	@./scripts/secrets_check.sh

docker-lint:
	@./scripts/docker_lint.sh

docker-lock:
	@./scripts/docker_lock_check.sh

smell:
	@./scripts/smell_check.sh

quality:
	@./scripts/quality_gate.sh

test-unit:
	$(VENV_ACTIVATE) && pytest tests_bootstrap/unit

test-integration:
	@./scripts/integration_check.sh

test-e2e:
	$(VENV_ACTIVATE) && pytest tests_bootstrap/e2e -m e2e

test-fault:
	@./scripts/fault_check.sh

coverage:
	@./scripts/coverage_gate.sh

up:
	docker compose up --build -d

down:
	docker compose down -v --remove-orphans

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

wait-ready:
	@./scripts/wait_ready.sh

capture-logs:
	@./scripts/capture_runtime_logs.sh "$(LOG_DIR)"

demo:
	@./utils/demo.sh

demo-python:
	$(VENV_ACTIVATE) && python ./utils/demo.py

scenarios:
	$(VENV_ACTIVATE) && python ./scripts/run_scenarios.py

loadtest:
	$(VENV_ACTIVATE) && python ./scripts/load_harness.py

capacity-model:
	$(VENV_ACTIVATE) && python ./scripts/capacity_model.py

prove:
	@./scripts/full_stack_check.sh

full-check: prove
