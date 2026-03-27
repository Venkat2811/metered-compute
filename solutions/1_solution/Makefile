SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

REPO_ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
VENV_ACTIVATE := source "$(REPO_ROOT).venv/bin/activate"
LOG_DIR ?= $(REPO_ROOT)worklog/evidence/latest

.PHONY: help venv sync fmt lint type security deps-audit secrets docker-lint docker-lock docker-lock-runtime smell quality \
		test-unit test-integration test-e2e test-fault test-all coverage \
		up down logs ps wait-ready capture-logs demo demo-python scenarios prove \
		full-check complexity load-profiles loadtest capacity-model benchmark-patterns webhook-replay \
		gate-unit gate-integration gate-fault

help:
	@echo "Targets:"
	@echo "  venv              Create Python 3.12 virtual environment"
	@echo "  sync              Sync dependencies with uv"
	@echo "  fmt               Check formatting with ruff"
	@echo "  lint              Run ruff lint checks"
	@echo "  type              Run mypy strict type checks"
	@echo "  complexity        Run complexity/function-size gate"
	@echo "  security          Run bandit static security checks"
	@echo "  deps-audit        Run pip-audit dependency vulnerability check"
	@echo "  secrets           Run detect-secrets baseline checks"
	@echo "  docker-lint       Run hadolint on Dockerfiles"
	@echo "  docker-lock       Verify Dockerfiles use uv.lock-aware install flow"
	@echo "  docker-lock-runtime Build API image and verify runtime package versions match lock"
	@echo "  smell             Run radon code-smell gate (complexity/maintainability)"
	@echo "  quality           Run full quality gate"
	@echo "  test-unit         Run unit tests"
	@echo "  test-integration  Run integration tests"
	@echo "  test-e2e          Run e2e tests"
	@echo "  test-fault        Run fault tests"
	@echo "  test-all          Run all tests"
	@echo "  coverage          Run coverage gate"
	@echo "  up                Start compose stack"
	@echo "  down              Stop compose stack"
	@echo "  logs              Tail compose logs"
	@echo "  ps                Show compose services"
	@echo "  wait-ready        Block until /ready reports ready=true"
	@echo "  capture-logs      Capture compose + metrics logs to LOG_DIR"
	@echo "  demo              Run demo script"
	@echo "  demo-python       Run Python demo script"
	@echo "  scenarios         Run scenario harness (13 scenarios)"
	@echo "  load-profiles     Run deterministic load/stress profiles"
	@echo "  loadtest          Sustained load test (default: 100 RPS × 30s)"
	@echo "  capacity-model    Build capacity projection from latest load profile report"
	@echo "  benchmark-patterns Benchmark write consistency patterns"
	@echo "  webhook-replay    Replay webhook dead-letter events back to pending queue"
	@echo "  prove             Single-command full verification (quality+coverage+stack tests+scenarios)"
	@echo "  full-check        Alias for prove"
	@echo "  gate-unit         Run unit quality gate script"
	@echo "  gate-integration  Run integration gate script"
	@echo "  gate-fault        Run fault gate script"

venv:
	uv venv --python 3.12 .venv

sync:
	$(VENV_ACTIVATE) && uv sync --dev

fmt:
	$(VENV_ACTIVATE) && ruff format --check .

lint:
	$(VENV_ACTIVATE) && ruff check .

type:
	$(VENV_ACTIVATE) && mypy --strict src tests

complexity:
	$(VENV_ACTIVATE) && python ./scripts/complexity_gate.py

security:
	$(VENV_ACTIVATE) && bandit -q -c pyproject.toml -r src -x tests

deps-audit:
	$(VENV_ACTIVATE) && pip-audit --ignore-vuln GHSA-5239-wwwm-4pmq

secrets:
	@./scripts/secrets_check.sh

docker-lint:
	@./scripts/docker_lint.sh

docker-lock:
	@./scripts/docker_lock_check.sh

docker-lock-runtime:
	@DOCKER_LOCK_RUNTIME_CHECK=1 ./scripts/docker_lock_check.sh

smell:
	@./scripts/smell_check.sh

quality:
	@./scripts/quality_gate.sh

test-unit:
	$(VENV_ACTIVATE) && pytest tests/unit

test-integration:
	@./scripts/integration_check.sh

test-e2e:
	$(VENV_ACTIVATE) && pytest tests/e2e -m e2e

test-fault:
	@./scripts/fault_check.sh

test-all:
	$(VENV_ACTIVATE) && pytest tests/

coverage:
	@./scripts/coverage_gate.sh

up:
	docker compose up --build -d

down:
	docker compose down

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

load-profiles:
	$(VENV_ACTIVATE) && python ./scripts/load_harness.py

loadtest:
	$(VENV_ACTIVATE) && python ./scripts/loadtest.py $(LOADTEST_ARGS)

capacity-model:
	$(VENV_ACTIVATE) && python ./scripts/capacity_model.py

benchmark-patterns:
	$(VENV_ACTIVATE) && python ./scripts/benchmark_write_patterns.py

webhook-replay:
	$(VENV_ACTIVATE) && python ./scripts/replay_webhook_dlq.py

prove:
	@./scripts/full_stack_check.sh

full-check: prove

gate-unit:
	@./scripts/ci_check.sh

gate-integration:
	@./scripts/integration_check.sh

gate-fault:
	@./scripts/fault_check.sh
