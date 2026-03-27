# P1-005 Solution 5 — Immediate Failure Release and Fault Proof (DONE)

Objective:

When external compute fails or times out, Solution 5 should void the pending TigerBeetle hold immediately instead of relying on the 300-second auto-timeout window. Prove that behavior with unit and live fault tests.

Acceptance criteria:

- [x] Compute failure and compute timeout paths both attempt immediate `VOID_PENDING_TRANSFER`.
- [x] Workflow still records deterministic terminal task status (`FAILED`) on those paths.
- [x] Live fault test proves user balance returns immediately after compute-plane failure.
- [x] Solution 5 proof posture includes the new fault coverage and still passes cleanly.

Checklist:

- [x] Add red unit tests for `billing.release_credits(...)` on compute failure and timeout.
- [x] Add red live fault test that stops compute and verifies immediate balance release.
- [x] Implement release-on-failure in `src/solution5/workflows.py` with replay-safe semantics.
- [x] Update proof harness and docs if fault phase or counts change.
- [x] Run targeted tests, `make quality`, and `make prove`.
