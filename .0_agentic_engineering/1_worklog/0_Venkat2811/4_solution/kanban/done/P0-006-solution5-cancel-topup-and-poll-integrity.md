# P0-006 Solution 4 - cancel, top-up, and poll integrity

Objective:

Close the remaining Solution 4 correctness gaps around late cancellation, JSON result fidelity, and admin credit idempotency before calling the solution complete.

Acceptance criteria:

- [x] A task cannot be left stuck in `CANCEL_REQUESTED` after credits were already captured.
- [x] Poll responses preserve structured JSON results instead of Python repr strings.
- [x] Admin top-up retries are idempotent when a caller reuses the same retry identity.
- [x] Admin top-up rejects unknown target users before mirroring balance state.
- [x] Add unit/integration coverage for each corrected path.
- [x] Refresh stale LOC/tree/docs claims for Solution 4 and shared docs.

TDD order:

1. Add red tests for late-cancel-after-capture behavior.
2. Add red tests for poll result JSON fidelity.
3. Add red tests for idempotent admin top-up retry and unknown-user rejection.
4. Implement workflow/API/repository fixes.
5. Refresh docs and run targeted proof commands.
