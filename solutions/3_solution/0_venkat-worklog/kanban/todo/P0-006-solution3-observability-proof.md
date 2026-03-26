# P0-006 Solution 3 - Observability, Scenarios, and Proof Gates

Objective:

Finish Sol 3 with production-observable signals and full proof posture.

Acceptance criteria:

- [ ]  Prometheus metrics + Grafana dashboards cover core control plane and worker flows.
- [ ]  Scenario harness covers all critical flows and is deterministic.
- [ ]  `make prove` executes all intended test tiers and captures evidence.
- [ ]  RFC/README claims match code behavior.

TDD order:

1. Add tests for metrics registration and route-level counters/histograms.
2. Add scenario tests first for coverage of critical paths in script form.
3. Wire commands and validate proof commands are runnable and bounded.

Checklist:

- [ ] Add `src/solution3/observability/metrics.py` and `src/solution3/observability/logging.py`.
- [ ] Add Prometheus metric suite:
  - submit attempts, success/failure
  - queue depths/lags, worker active
  - TB reserve/post/void timings
  - reconciler and projector lag/errors.
- [ ] Add/adjust Grafana dashboards and alert rules.
- [ ] Add script updates:
  - `scripts/run_scenarios.py`
  - `scripts/benchmark` or load harness entrypoint
  - `scripts/capacity_model.py`
  - `scripts/full_stack_check.sh`
- [ ] Add tests for scenario loader and output shape.
- [ ] Add evidence directory convention and timestamps for prove runs.
- [ ] Align `README.md`, solution matrix row, and RFC status notes.

Completion criteria:

- [ ] `make prove` passes from clean state on a full run.
- [ ] Evidence directory contains full-check output, scenario report, and logs.
