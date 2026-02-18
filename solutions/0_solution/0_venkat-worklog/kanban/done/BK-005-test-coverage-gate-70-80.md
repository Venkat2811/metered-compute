# BK-005: Coverage Gate (70-80%)

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Add explicit coverage gates with realistic thresholds for Solution 0 scope and protect critical reliability paths.

## Checklist

- [x] Add `pytest-cov` reporting in CI and local gate scripts
- [x] Set global coverage gate target in the 70-80 range (initial target: 75%)
- [x] Set critical-module floor (initial target: 80% for `app`, `services/billing`, `worker_tasks`, `reaper`)
- [x] Add missing tests for uncovered branches in compensation and degradation paths
- [x] Publish coverage report artifact in `worklog/baselines/`

## Exit Criteria

- [x] Coverage gate is enforced and cannot regress silently
- [x] Critical reliability modules are above agreed floor
- [x] Coverage report is reproducible locally and in CI

## Evidence

- Gate command: `./scripts/coverage_gate.sh`
- CI/unit gate wiring: `./scripts/ci_check.sh`
- Latest artifact:
  - `../../baselines/coverage-latest.json`
  - `../../baselines/coverage-latest.xml`
- Latest measured totals:
  - Global: `81.87%`
  - `src/solution0/app.py`: `82.7%`
  - `src/solution0/services/billing.py`: `100.0%`
  - `src/solution0/worker_tasks.py`: `92.6%`
  - `src/solution0/reaper.py`: `94.1%`
