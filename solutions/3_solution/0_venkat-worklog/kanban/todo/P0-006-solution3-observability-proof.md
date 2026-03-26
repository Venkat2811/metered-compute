# P0-006 Solution 3 - Observability, Scenarios, and Proof Gates

Objective:

Finish Sol 3 with production-observable signals and full proof posture.

Acceptance criteria:

- [ ]  Prometheus metrics + Grafana dashboards cover core control plane and worker flows.
- [ ]  Scenario harness covers all critical flows and is deterministic.
- [x]  `make prove` executes all intended bootstrap test tiers and captures evidence.
- [x]  README claims for the current bootstrap scope match code behavior.

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
  - `scripts/full_stack_check.sh` (bootstrap proof gate is green as of `full-check-20260326T223430Z`)
- [ ] Add tests for scenario loader and output shape.
- [x] Add evidence directory convention and timestamps for prove runs.
- [x] Align `README.md` to the current shipped bootstrap scope.
- [ ] Align solution matrix row and RFC status notes.

Completion criteria:

- [x] `make prove` passes from clean state on the current bootstrap run.
- [ ] Evidence directory contains full-check output, scenario report, and logs.
