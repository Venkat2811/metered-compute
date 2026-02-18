# BK-023: Docker Reproducible Builds with uv.lock

Priority: Backlog
Status: done
Depends on: P0-001

## Objective

Ensure container builds respect the lockfile and match local `uv` dependency resolution.

## Checklist

- [x] Move Dockerfile install path from `pip install .` to lock-respecting `uv` workflow
- [x] Verify deterministic dependency graph inside images
- [x] Add CI check that compares runtime lock fidelity

## Exit Criteria

- [x] Docker images are reproducible from the committed lock state

## Evidence

- All service Dockerfiles use `uv sync --frozen --no-dev`: `docker/api/Dockerfile`, `docker/worker/Dockerfile`, `docker/reaper/Dockerfile`
- Static + runtime lock-fidelity verification script added: `scripts/docker_lock_check.sh`
- Quality gate now enforces static lock checks: `scripts/quality_gate.sh`
- Full prove flow includes runtime lock-fidelity check artifact: `scripts/full_stack_check.sh`
- Make targets added: `Makefile` (`docker-lock`, `docker-lock-runtime`)
