#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --dev" >&2
  exit 1
fi

source .venv/bin/activate

cc_output="$(radon cc src/solution2 -s -n E || true)"
if [[ -n "${cc_output}" ]]; then
  echo "smell gate failed: cyclomatic complexity grade E/F detected" >&2
  echo "${cc_output}" >&2
  exit 1
fi

mi_output="$(radon mi src/solution2 -s -n C || true)"
if [[ -n "${mi_output}" ]]; then
  echo "smell gate failed: maintainability index grade C/D/F detected" >&2
  echo "${mi_output}" >&2
  exit 1
fi

echo "smell gate passed"
