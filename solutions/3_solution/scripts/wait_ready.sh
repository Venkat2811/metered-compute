#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://localhost:8000}"
timeout_seconds="${2:-180}"
interval_seconds="${3:-2}"

deadline=$((SECONDS + timeout_seconds))

while ((SECONDS < deadline)); do
  if response="$(curl -fsS "${base_url}/ready" 2>/dev/null)"; then
    ready="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("ready", False))' "${response}" 2>/dev/null || echo "False")"
    if [[ "${ready}" == "True" || "${ready}" == "true" ]]; then
      echo "ready: ${response}"
      exit 0
    fi
  fi
  sleep "${interval_seconds}"
done

echo "timed out waiting for ${base_url}/ready" >&2
exit 1
