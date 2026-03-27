SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

REPO_ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
VENV_ACTIVATE := source "$(REPO_ROOT).venv/bin/activate"
LOG_DIR ?= $(REPO_ROOT)worklog/evidence/latest

.PHONY: help venv sync fmt lint type security smell quality \
		test-unit test-integration test-all coverage \
		up down build logs ps wait-ready capture-logs reset-state demo scenarios loadtest \
		prove full-check clean

help:
	@echo "Targets:"
	@echo "  venv              Create Python 3.12 virtual environment"
	@echo "  sync              Sync dependencies with uv"
	@echo "  fmt               Check formatting with ruff"
	@echo "  lint              Run ruff lint checks"
	@echo "  type              Run mypy strict type checks"
	@echo "  security          Run bandit static security checks"
	@echo "  smell             Run radon code-smell gate (complexity/maintainability)"
	@echo "  quality           Run full quality gate"
	@echo "  test-unit         Run unit tests"
	@echo "  test-integration  Run integration tests (requires compose stack)"
	@echo "  test-all          Run all tests"
	@echo "  coverage          Run coverage gate"
	@echo "  up                Start compose stack"
	@echo "  down              Stop compose stack"
	@echo "  logs              Tail compose logs"
	@echo "  ps                Show compose services"
	@echo "  wait-ready        Block until /ready reports ok"
	@echo "  capture-logs      Capture compose + metrics logs to LOG_DIR"
	@echo "  reset-state       Reset PG+Redis state for clean test runs"
	@echo "  demo              Run demo script"
	@echo "  scenarios         Run 13 scenario checks against live stack"
	@echo "  loadtest          Sustained load test (default: 100 RPS × 30s)"
	@echo "  prove             Single-command full verification (quality+coverage+stack+integration+scenarios)"
	@echo "  full-check        Alias for prove"
	@echo "  clean             Tear down and remove images"

venv:
	uv venv --python 3.12 .venv

sync:
	$(VENV_ACTIVATE) && uv pip install -e ".[dev]"

fmt:
	$(VENV_ACTIVATE) && ruff format --check .

lint:
	$(VENV_ACTIVATE) && ruff check .

type:
	$(VENV_ACTIVATE) && mypy --strict src tests

security:
	$(VENV_ACTIVATE) && bandit -q -c pyproject.toml -r src -x tests

smell:
	@./scripts/smell_check.sh

quality:
	@./scripts/quality_gate.sh

test-unit:
	$(VENV_ACTIVATE) && pytest tests/unit -v

test-integration:
	$(VENV_ACTIVATE) && INTEGRATION=1 pytest tests/integration -v

test-all:
	$(VENV_ACTIVATE) && INTEGRATION=1 pytest tests/ -v

coverage:
	@./scripts/coverage_gate.sh

up:
	docker compose up --build -d

down:
	docker compose down -v

build:
	docker compose build

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

wait-ready:
	@./scripts/wait_ready.sh

capture-logs:
	@./scripts/capture_runtime_logs.sh "$(LOG_DIR)"

reset-state:
	@./scripts/reset_state.sh

demo:
	@bash scripts/demo.sh

scenarios:
	$(VENV_ACTIVATE) && python scripts/run_scenarios.py

loadtest:
	$(VENV_ACTIVATE) && python scripts/loadtest.py $(LOADTEST_ARGS)

prove:
	@./scripts/full_stack_check.sh

full-check: prove

clean:
	docker compose down -v --rmi local
