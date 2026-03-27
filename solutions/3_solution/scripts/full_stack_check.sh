#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --dev" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
artifact_dir="${repo_root}/worklog/evidence/full-check-${timestamp}"
mkdir -p "${artifact_dir}"

status="failed"

on_exit() {
  local exit_code=$?
  local final_status="failed"
  if [[ ${exit_code} -eq 0 && "${status}" == "passed" ]]; then
    final_status="passed"
  fi

  ./scripts/capture_runtime_logs.sh "${artifact_dir}" >/dev/null 2>&1 || true
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  printf "{\n  \"status\": \"%s\",\n  \"artifact_dir\": \"%s\"\n}\n" \
    "${final_status}" "${artifact_dir}" >"${artifact_dir}/summary.json"
}

trap on_exit EXIT

source .venv/bin/activate

./scripts/quality_gate.sh | tee "${artifact_dir}/quality.log"
./scripts/coverage_gate.sh | tee "${artifact_dir}/coverage.log"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
docker compose up --build -d | tee "${artifact_dir}/compose-up.log"
./scripts/wait_ready.sh "http://localhost:8000" 240 2 | tee "${artifact_dir}/ready.log"

pytest tests_bootstrap/integration -m integration | tee "${artifact_dir}/integration.log"
pytest tests_bootstrap/e2e -m e2e | tee "${artifact_dir}/e2e.log"
python ./scripts/run_scenarios.py --output "${artifact_dir}/scenarios.json" | tee "${artifact_dir}/scenarios.log"
./scripts/fault_check.sh | tee "${artifact_dir}/fault.log"

status="passed"
echo "bootstrap full check artifacts: ${artifact_dir}"
