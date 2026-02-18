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

pytest \
  tests/unit \
  tests/fault/test_publish_failure_path.py \
  --cov=src/solution1 \
  --cov-report=term-missing \
  --cov-report=xml:"${coverage_report_xml}" \
  --cov-report=json:"${coverage_report_json}" \
  --cov-fail-under=75

python - <<'PY'
import json
from pathlib import Path

report = Path("worklog/baselines/coverage-latest.json")
data = json.loads(report.read_text())
files = data["files"]

critical = {
    "src/solution1/app.py": 80.0,
    "src/solution1/services/billing.py": 80.0,
    "src/solution1/workers/stream_worker.py": 55.0,
    "src/solution1/workers/reaper.py": 80.0,
}

violations = []
for path, minimum in critical.items():
    measured = float(files[path]["summary"]["percent_covered"])
    if measured < minimum:
        violations.append((path, measured, minimum))

if violations:
    lines = ["critical module coverage floor failures:"]
    for path, measured, minimum in violations:
        lines.append(f"- {path}: {measured:.1f}% < {minimum:.1f}%")
    raise SystemExit("\n".join(lines))
PY
