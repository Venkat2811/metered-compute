#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --dev" >&2
  exit 1
fi

source .venv/bin/activate

coverage_report_dir="worklog/baselines"
coverage_report_json="${coverage_report_dir}/coverage-latest.json"
coverage_report_xml="${coverage_report_dir}/coverage-latest.xml"

mkdir -p "${coverage_report_dir}"

pytest \
  tests_bootstrap/unit \
  --cov=src/solution3 \
  --cov-report=term-missing \
  --cov-report=xml:"${coverage_report_xml}" \
  --cov-report=json:"${coverage_report_json}" \
  --cov-fail-under=85
