#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

dockerfiles=(
  "docker/api/Dockerfile"
  "docker/dispatcher/Dockerfile"
  "docker/reconciler/Dockerfile"
  "docker/worker/Dockerfile"
  "docker/outbox_relay/Dockerfile"
  "docker/projector/Dockerfile"
  "docker/webhook_worker/Dockerfile"
  "docker/postgres/Dockerfile"
)

if command -v hadolint >/dev/null 2>&1; then
  hadolint --ignore DL3008 "${dockerfiles[@]}"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "hadolint and docker are unavailable; cannot lint Dockerfiles" >&2
  exit 1
fi

for dockerfile in "${dockerfiles[@]}"; do
  docker run --rm -i hadolint/hadolint hadolint --ignore DL3008 - < "${dockerfile}"
done
