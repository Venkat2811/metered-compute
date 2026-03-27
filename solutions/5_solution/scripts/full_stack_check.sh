#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: make venv && make sync" >&2
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
  printf '{"status": "%s", "artifact_dir": "%s"}\n' \
    "${final_status}" "${artifact_dir}" >"${artifact_dir}/summary.json"

  echo ""
  echo "=========================================="
  echo "  full-check: ${final_status}"
  echo "  artifacts:  ${artifact_dir}"
  echo "=========================================="
}

trap on_exit EXIT

source .venv/bin/activate

# ── Phase 1: quality gate ──
echo "── Phase 1: quality gate ──"
./scripts/quality_gate.sh 2>&1 | tee "${artifact_dir}/quality.log"

# ── Phase 2: coverage gate ──
echo "── Phase 2: coverage gate ──"
./scripts/coverage_gate.sh 2>&1 | tee "${artifact_dir}/coverage.log"

# ── Phase 3: docker compose stack ──
echo "── Phase 3: docker compose up ──"
docker compose down -v --remove-orphans >/dev/null 2>&1 || true
docker compose up --build -d 2>&1 | tee "${artifact_dir}/compose-up.log"
./scripts/wait_ready.sh "http://localhost:8000" 120 2 | tee -a "${artifact_dir}/compose-up.log"

# Wait for Restate to discover the TaskService (background registration)
echo "Waiting for Restate service registration..."
for i in $(seq 1 30); do
  deployments="$(curl -sf http://localhost:9070/deployments 2>/dev/null || echo '{}')"
  if echo "${deployments}" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('deployments') else 1)" 2>/dev/null; then
    echo "Restate TaskService registered"
    break
  fi
  sleep 2
done

# ── Phase 4: integration tests ──
echo "── Phase 4: integration tests ──"
./scripts/reset_state.sh
INTEGRATION=1 pytest tests/integration -v 2>&1 | tee "${artifact_dir}/integration.log"

# ── Phase 5: scenarios ──
echo "── Phase 5: scenarios (13 scenarios) ──"
./scripts/reset_state.sh
python scripts/run_scenarios.py --output "${artifact_dir}/scenarios.json" 2>&1 | tee "${artifact_dir}/scenarios.log"

status="passed"
echo "full stack check artifacts: ${artifact_dir}"
