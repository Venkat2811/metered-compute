#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

output_dir="${1:-${repo_root}/worklog/evidence/latest}"
service_logs_dir="${output_dir}/services"

mkdir -p "${service_logs_dir}"

docker compose ps >"${output_dir}/compose-ps.txt" 2>&1 || true
docker compose logs --no-color >"${output_dir}/compose.log" 2>&1 || true

for svc in api postgres redis tigerbeetle restate prometheus grafana; do
  docker compose logs --no-color "${svc}" >"${service_logs_dir}/${svc}.log" 2>&1 || true
done

curl -sS "http://localhost:8000/health" >"${output_dir}/health.json" 2>/dev/null || true
curl -sS "http://localhost:8000/ready" >"${output_dir}/ready.json" 2>/dev/null || true
curl -sS "http://localhost:8000/metrics" >"${output_dir}/api-metrics.prom" 2>/dev/null || true
curl -sS "http://localhost:9090/api/v1/targets" >"${output_dir}/prom-targets.json" 2>/dev/null || true

echo "logs captured to ${output_dir}"
