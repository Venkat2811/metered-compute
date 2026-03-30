#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://localhost:8000}"
timeout_seconds="${2:-120}"
interval_seconds="${3:-2}"

deadline=$((SECONDS + timeout_seconds))

while ((SECONDS < deadline)); do
  http_code="$(curl -so /dev/null -w '%{http_code}' "${base_url}/ready" 2>/dev/null || echo "000")"
  if [[ "${http_code}" == "200" ]]; then
    response="$(curl -fsS "${base_url}/ready" 2>/dev/null)"
    echo "ready: ${response}"
    exit 0
  fi
  sleep "${interval_seconds}"
done

echo "timed out waiting for ${base_url}/ready after ${timeout_seconds}s" >&2
exit 1
