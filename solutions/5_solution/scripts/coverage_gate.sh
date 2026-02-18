#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ ! -d .venv ]]; then
  echo "missing .venv. run: make venv && make sync" >&2
  exit 1
fi

source .venv/bin/activate

coverage_report_dir="worklog/baselines"
coverage_report_json="${coverage_report_dir}/coverage-latest.json"
coverage_report_xml="${coverage_report_dir}/coverage-latest.xml"

mkdir -p "${coverage_report_dir}"

# app.py is a FastAPI factory — covered by integration tests, not unit tests.
# workflows.py execute_task is a Restate handler — covered by integration tests.
pytest \
  tests/unit \
  --cov=src/solution5 \
  --cov-report=term-missing \
  --cov-report=xml:"${coverage_report_xml}" \
  --cov-report=json:"${coverage_report_json}" \
  --cov-fail-under=35

# Per-module coverage floors gate on critical (unit-testable) modules.
# app.py and workflows.execute_task are excluded — they require a live stack.

python3 - <<'PY'
import json
from pathlib import Path

report = Path("worklog/baselines/coverage-latest.json")
data = json.loads(report.read_text())
files = data["files"]

# Critical modules that require higher coverage floors
critical = {
    "src/solution5/billing.py": 70.0,
    "src/solution5/cache.py": 80.0,
    "src/solution5/repository.py": 80.0,
}

violations = []
for path, minimum in critical.items():
    if path not in files:
        violations.append((path, 0.0, minimum))
        continue
    measured = float(files[path]["summary"]["percent_covered"])
    if measured < minimum:
        violations.append((path, measured, minimum))

if violations:
    lines = ["critical module coverage floor failures:"]
    for path, measured, minimum in violations:
        lines.append(f"- {path}: {measured:.1f}% < {minimum:.1f}%")
    raise SystemExit("\n".join(lines))

print("coverage gate passed")
PY
