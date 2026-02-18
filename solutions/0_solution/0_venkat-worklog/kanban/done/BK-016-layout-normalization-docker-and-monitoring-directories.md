# BK-016: Layout Normalization for Docker and Monitoring Directories

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Normalize repository layout to explicitly group runtime/container assets under `docker/` and observability assets under `monitoring/` without changing behavior.

## Checklist

- [x] Propose target directory tree:
  - [x] `docker/api/Dockerfile`, `docker/worker/Dockerfile`, `docker/reaper/Dockerfile`
  - [x] `monitoring/prometheus/*`, `monitoring/grafana/*`
- [x] Update compose paths and build contexts for the new structure
- [x] Add migration note to README and runbook with old->new path mapping
- [x] Verify no behavior drift through full gates (`ci`, `integration`, `fault`, demo)

## Exit Criteria

- [x] Directory structure clearly separates app code vs deployment assets
- [x] Docker Desktop/Compose UX remains clean and reproducible
- [x] All gates remain green after the path migration

## Progress Notes (2026-02-15)

Implemented:

- moved container assets:
  - `api/` -> `docker/api/`
  - `worker/` -> `docker/worker/`
  - `reaper/` -> `docker/reaper/`
- moved observability assets:
  - `prometheus/` -> `monitoring/prometheus/`
  - `grafana/` -> `monitoring/grafana/`
- rewired compose and docs:
  - `compose.yaml` build and volume paths updated
  - `README.md` repository layout and observability path references updated
  - `worklog/RUNBOOK.md` old->new path mapping added

Evidence:

- `./scripts/ci_check.sh` passed (`19 passed`)
- `./scripts/integration_check.sh` passed (`7 integration`, `1 e2e`)
- `./scripts/fault_check.sh` passed (`4 passed`)
