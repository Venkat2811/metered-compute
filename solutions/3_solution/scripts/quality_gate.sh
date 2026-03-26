#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --dev" >&2
  exit 1
fi

source .venv/bin/activate

ruff format --check src tests_bootstrap
ruff check src tests_bootstrap
mypy --strict src tests_bootstrap
bandit -q -c pyproject.toml -r src -x tests_bootstrap
# Pygments is a transitive dependency of tooling in this bootstrap stage. As of
# 2026-03-26, pip-audit reports GHSA-5239-wwwm-4pmq with no fixed release.
pip-audit --ignore-vuln GHSA-5239-wwwm-4pmq
./scripts/secrets_check.sh
./scripts/docker_lint.sh
./scripts/docker_lock_check.sh
./scripts/smell_check.sh
