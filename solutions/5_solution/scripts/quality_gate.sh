#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: make venv && make sync" >&2
  exit 1
fi

source .venv/bin/activate

echo "── ruff format ──"
ruff format --check .

echo "── ruff check ──"
ruff check .

echo "── mypy ──"
mypy --strict src tests

echo "── bandit ──"
bandit -q -c pyproject.toml -r src -x tests

echo "── pip-audit ──"
pip-audit --ignore-vuln GHSA-5239-wwwm-4pmq

echo "── secrets check ──"
./scripts/secrets_check.sh

echo "── smell check ──"
./scripts/smell_check.sh

echo "quality gate passed"
