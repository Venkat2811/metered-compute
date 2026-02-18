# P0-008: Demo Script and Release Readiness

Priority: P0
Status: done
Depends on: P0-007

## Objective

Deliver reproducible demo and release-readiness evidence for Solution 0.

## Checklist

- [x] Add deterministic demo script (`submit -> poll until terminal`)
- [x] Add admin top-up and insufficient-credit scenarios in demo docs
- [x] Capture baseline evidence using `worklog/baselines/TEMPLATE.md`
- [x] Validate release gates (`unit`, `integration`, `fault`, observability)
- [x] Update `0_solution/README.md` with run/test instructions

## TDD Subtasks

1. Red

- [x] Add failing e2e test that asserts demo script output contract

2. Green

- [x] Implement demo script and docs until e2e passes

3. Refactor

- [x] Reduce demo script complexity; keep readable and deterministic

## Acceptance Criteria

- [x] New engineer can run demo from clean setup in one pass
- [x] All release gates pass and artifact is recorded
- [x] Solution 0 is ready for demo review

## Progress Notes (2026-02-15)

Implemented:

- demo artifacts:
  - `utils/demo.sh`
  - `tests/e2e/test_demo_script.py`
- documentation:
  - `README.md` (setup, run, API/demo flows, test gates)
- release evidence:
  - `worklog/baselines/2026-02-15-solution0-baseline.md`
  - `worklog/baselines/latest-release-gate.json`

Evidence:

- `./utils/demo.sh` exits `0` and reaches terminal `COMPLETED`
- `./scripts/ci_check.sh` pass
- `./scripts/integration_check.sh` pass
- `./scripts/fault_check.sh` pass (`4` fault scenarios)
