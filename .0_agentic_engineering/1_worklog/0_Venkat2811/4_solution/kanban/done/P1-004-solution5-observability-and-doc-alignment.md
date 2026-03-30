# P1-004 Solution 4 — Observability and Doc Alignment (DONE)

Objective:

Make Solution 4's Prometheus + Grafana claim fully real for the shipped external-compute architecture, and align README/RFC/root-matrix wording to the actual system shape.

Acceptance criteria:

- [x] Compute service exposes a real `/metrics` endpoint with useful counters/histograms.
- [x] API request latency metrics are actually recorded, not just defined.
- [x] Prometheus scrapes both `api` and `compute`.
- [x] Grafana provisioning and a real Solution 4 dashboard are checked in.
- [x] Solution 4 README reflects external compute, 13 scenarios, and honest container counts.
- [x] RFC-0004 reflects external compute instead of inline compute for the shipped implementation.
- [x] Root `solutions/README.md` entries for Solution 4 match the shipped surface.
- [x] `make prove` passes from a clean state after the alignment slice.

Checklist:

- [x] Add red tests for compute metrics endpoint and Prometheus target coverage.
- [x] Add red test for checked-in monitoring assets/provisioning.
- [x] Instrument compute worker metrics and API HTTP duration metrics.
- [x] Update Prometheus config and compose mounts.
- [x] Add Grafana datasource/dashboard provisioning and a real dashboard JSON.
- [x] Reconcile README/RFC/root-matrix language with external compute and current counts.
- [x] Run targeted tests, quality gate, and full proof.
