#!/usr/bin/env bash
set -euo pipefail

base_url="${BASE_URL:-http://localhost:8000}"

health_response="$(curl -fsS "${base_url}/health")"
echo "health: ${health_response}"

ready_response="$(curl -fsS "${base_url}/ready")"
echo "ready: ${ready_response}"

ready_flag="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("ready", False))' "${ready_response}")"
if [[ "${ready_flag}" != "True" && "${ready_flag}" != "true" ]]; then
  echo "solution3 bootstrap demo expected ready=true" >&2
  exit 1
fi
