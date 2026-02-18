#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

dockerfiles=(
  "docker/api/Dockerfile"
  "docker/worker/Dockerfile"
  "docker/reaper/Dockerfile"
)

for dockerfile in "${dockerfiles[@]}"; do
  if ! rg -q "uv sync --frozen --no-dev" "${dockerfile}"; then
    echo "lock check failed: ${dockerfile} must use uv sync --frozen --no-dev" >&2
    exit 1
  fi
  if rg -q "pip install --no-cache-dir \\." "${dockerfile}"; then
    echo "lock check failed: ${dockerfile} still installs project via pip install ." >&2
    exit 1
  fi
done

if [[ "${DOCKER_LOCK_RUNTIME_CHECK:-0}" != "1" ]]; then
  echo "docker lock static checks passed"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker lock runtime check requires docker in PATH" >&2
  exit 1
fi

python_cmd="$(command -v python3 || true)"
if [[ -z "${python_cmd}" ]]; then
  python_cmd="$(command -v python || true)"
fi
if [[ -z "${python_cmd}" ]]; then
  echo "docker lock runtime check requires python3 or python in PATH" >&2
  exit 1
fi

expected_json_file="$(mktemp)"
trap 'rm -f "${expected_json_file}"' EXIT

"${python_cmd}" - <<'PY' >"${expected_json_file}"
from __future__ import annotations

import json
import pathlib
import tomllib

project = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
dependencies: dict[str, str] = {}
for entry in project["project"]["dependencies"]:
    if "==" not in entry:
        continue
    name, version = entry.split("==", 1)
    normalized_name = name.split("[", 1)[0].strip().lower()
    dependencies[normalized_name] = version.strip()
print(json.dumps(dependencies, sort_keys=True))
PY

image_tag="mc-solution0-lockcheck:local"
docker build -f docker/api/Dockerfile -t "${image_tag}" . >/dev/null

actual_json="$(
  docker run --rm -i "${image_tag}" python - <<'PY'
from __future__ import annotations

import importlib.metadata
import json

packages = [
    "fastapi",
    "uvicorn",
    "celery",
    "redis",
    "asyncpg",
    "pydantic",
    "pydantic-settings",
    "structlog",
    "prometheus-client",
    "orjson",
    "uuid6",
]
versions = {package: importlib.metadata.version(package) for package in packages}
print(json.dumps(versions, sort_keys=True))
PY
)"

"${python_cmd}" - <<'PY' "${expected_json_file}" "${actual_json}"
from __future__ import annotations

import json
import pathlib
import sys

expected = json.loads(pathlib.Path(sys.argv[1]).read_text())
actual = json.loads(sys.argv[2])

mismatches: list[tuple[str, str, str]] = []
missing: list[str] = []

for package, actual_version in actual.items():
    expected_version = expected.get(package)
    if expected_version is None:
        missing.append(package)
        continue
    if expected_version != actual_version:
        mismatches.append((package, expected_version, actual_version))

if missing or mismatches:
    if missing:
        print("packages missing from lock expectation:", ", ".join(sorted(missing)))
    if mismatches:
        print("version mismatches detected:")
        for package, expected_version, actual_version in mismatches:
            print(f"  - {package}: expected {expected_version}, got {actual_version}")
    raise SystemExit(1)

print("docker lock runtime check passed")
PY
