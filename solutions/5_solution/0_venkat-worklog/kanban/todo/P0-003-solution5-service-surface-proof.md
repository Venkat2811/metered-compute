# P0-003 Solution 5 - Service Surface and Proof Posture

Objective:

Decide and implement the concrete scope decision for Sol 5 parity expansion, with matching tests and documentation.

Acceptance criteria:

- [ ] A scoped parity decision exists (API-key + optional JWT, or JWT parity) and is documented.
- [ ] Expanded capabilities are implemented only where they do not violate Sol 5 architecture.
- [ ] Full proof posture (`quality`, `coverage`, `prove`) reflects implemented scope.

TDD order:

1. Add regression tests for the capability choices first (e.g., scope mode, richer payloads, webhook/batch flows).
2. Implement only the signed-up capabilities, rejecting or rejecting-by-design everything else.
3. Add scenario/load proof updates and docs alignment in final step.

Checklist:

- [ ] Decision checkpoint:
  - either keep API-key auth + explicit role model,
  - or migrate to JWT/OAuth parity and document operational implications.
- [ ] Product expansion:
  - optional tiers/model classes,
  - optional sync/async mode,
  - optional batch submit,
  - webhook delivery path.
- [ ] Expand tests:
  - unit tests for auth branch and scope checks,
  - integration tests for new endpoints/flows,
  - scenario updates for supported path(s).
- [ ] Load/proving:
  - ensure `make loadtest` still meaningful after architecture change,
  - update scenario report naming.
- [ ] Update docs:
  - `README.md` to reflect actual scope and limits,
  - RFC-0005 alignment notes if claims changed.

Completion criteria:

- [ ] No undocumented feature promises.
- [ ] `make prove` artifacts correspond to the implemented Sol 5 scope.
