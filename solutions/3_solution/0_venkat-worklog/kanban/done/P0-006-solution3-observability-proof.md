# P0-006 Solution 3 - Observability, Scenarios, and Proof Gates

Objective:

Finish Sol 3 with production-observable signals and full proof posture.

Acceptance criteria:

- [x]  Prometheus metrics + Grafana dashboards cover core control plane and worker flows.
- [x]  Scenario harness covers all critical flows and is deterministic.
- [x]  `make prove` executes all intended bootstrap test tiers and captures evidence.
- [x]  README claims and solution matrix notes match current code behavior.

TDD order:

1. Add tests for metrics registration and route-level counters/histograms.
2. Add scenario tests first for coverage of critical paths in script form.
3. Add load/capacity tooling for reviewer validation and wire commands into the proof workflow.
4. Wire commands and validate proof commands are runnable and bounded.

Checklist:

- [x] Add `src/solution3/observability/metrics.py`.
- [x] Add Prometheus metric suite:
  - submit attempts, success/failure
  - dispatch / outbox / projector / webhook / reconciler signals
  - worker execution and terminal outcomes
- [x] Add/adjust Grafana dashboards and alert rules.
- [x] Add script updates:
  - [x] `scripts/run_scenarios.py`
  - [x] load harness entrypoint via `scripts/load_harness.py`
  - [x] `scripts/capacity_model.py`
  - [x] `scripts/full_stack_check.sh` now runs the scenario harness after the compose-backed test tiers
- [x] Add tests for scenario loader and output shape.
- [x] Add evidence directory convention and timestamps for prove runs.
- [x] Align `README.md` to the current shipped Solution 3 scope.
- [x] Align solution matrix row and status notes.

Completion criteria:

- [x] `make prove` passes from clean state on the current bootstrap run.
- [x] Evidence directory contains full-check output, scenario report, and logs.
