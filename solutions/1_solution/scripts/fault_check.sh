#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --dev" >&2
  exit 1
fi

source .venv/bin/activate

./scripts/reset_state.sh
pytest tests/fault/test_readiness_degradation.py -m fault

./scripts/reset_state.sh
pytest tests/fault/test_runtime_faults.py -m fault

./scripts/reset_state.sh
pytest tests/fault/test_publish_failure_path.py -m fault
