#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
if [[ -f "${repo_root}/.env.dev.defaults" ]]; then
  # shellcheck disable=SC1091
  source "${repo_root}/.env.dev.defaults"
fi

base_url="${BASE_URL:-http://localhost:8000}"
api_key="${API_KEY:-${ALICE_API_KEY:-}}"
if [[ -z "${api_key}" ]]; then
  echo "API key is not set (API_KEY or ALICE_API_KEY)" >&2
  exit 1
fi

token_response="$(curl -sS -X POST "${base_url}/v1/oauth/token" \
  -H "Content-Type: application/json" \
  -d "{\"api_key\":\"${api_key}\"}")"
access_token="$(python3 -c 'import json,sys; body=json.loads(sys.argv[1]); print(body.get("access_token",""))' "${token_response}")"
if [[ -z "${access_token}" ]]; then
  echo "OAuth token exchange failed: ${token_response}" >&2
  exit 1
fi

task_id=""
for _ in $(seq 1 20); do
  submit_response="$(curl -sS -X POST "${base_url}/v1/task" \
    -H "Authorization: Bearer ${access_token}" \
    -H "Content-Type: application/json" \
    -d '{"x": 5, "y": 3}')"

  echo "submit: ${submit_response}"

  parsed_task_id="$(python3 -c 'import json,sys; body=json.loads(sys.argv[1]); print(body.get("task_id",""))' "${submit_response}")"
  if [[ -n "${parsed_task_id}" ]]; then
    task_id="${parsed_task_id}"
    break
  fi

  error_code="$(python3 -c 'import json,sys; body=json.loads(sys.argv[1]); print(body.get("error",{}).get("code",""))' "${submit_response}")"
  if [[ "${error_code}" != "TOO_MANY_REQUESTS" && "${error_code}" != "SERVICE_DEGRADED" ]]; then
    echo "demo submit failed with non-retryable error" >&2
    exit 1
  fi
  sleep 1
done

if [[ -z "${task_id}" ]]; then
  echo "demo could not submit task within retry window" >&2
  exit 1
fi

for _ in $(seq 1 30); do
  poll_response="$(curl -sS -G "${base_url}/v1/poll" \
    --data-urlencode "task_id=${task_id}" \
    -H "Authorization: Bearer ${access_token}")"
  echo "poll: ${poll_response}"

  status="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("status",""))' "${poll_response}")"

  if [[ "${status}" == "COMPLETED" || "${status}" == "FAILED" || "${status}" == "CANCELLED" || "${status}" == "EXPIRED" ]]; then
    exit 0
  fi
  sleep 1
done

echo "demo timeout waiting for terminal status" >&2
exit 1
