#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --dev" >&2
  exit 1
fi

source .venv/bin/activate

baseline=".secrets.baseline"
refresh="${1:-}"
scan_paths=(
  "src"
  "docker"
  "scripts"
  "tests"
  "utils"
  "compose.yaml"
  "pyproject.toml"
  ".env.dev.defaults"
)

if [[ "${refresh}" == "--refresh" || ! -f "${baseline}" ]]; then
  detect-secrets scan "${scan_paths[@]}" > "${baseline}"
  if [[ "${refresh}" == "--refresh" ]]; then
    echo "refreshed ${baseline}"
  fi
  exit 0
fi

tmp_scan="$(mktemp)"
trap 'rm -f "${tmp_scan}"' EXIT
detect-secrets scan "${scan_paths[@]}" > "${tmp_scan}"

python - "${baseline}" "${tmp_scan}" <<'PY'
import json
import sys
from pathlib import Path

baseline_path = Path(sys.argv[1])
current_path = Path(sys.argv[2])

baseline_data = json.loads(baseline_path.read_text())
current_data = json.loads(current_path.read_text())

baseline_results = baseline_data.get("results", {})
current_results = current_data.get("results", {})

if baseline_results == current_results:
    raise SystemExit(0)

print("detect-secrets drift detected. run './scripts/secrets_check.sh --refresh' to update baseline.")

baseline_keys = set(baseline_results.keys())
current_keys = set(current_results.keys())

new_files = sorted(current_keys - baseline_keys)
if new_files:
    print("new files with potential secrets:")
    for item in new_files:
        print(f"- {item}")

changed_files = sorted(current_keys & baseline_keys)
for file_name in changed_files:
    baseline_entries = baseline_results[file_name]
    current_entries = current_results[file_name]
    if baseline_entries != current_entries:
        print(f"changed findings: {file_name}")

raise SystemExit(1)
PY
