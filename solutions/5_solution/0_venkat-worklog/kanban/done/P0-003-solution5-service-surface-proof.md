# P0-003 Solution 5 — Service Surface and Proof Posture (DONE)

Objective:

Set Sol 5 service surface as narrow and explicit for this architecture (API-key auth only), with matching validation, tests, and docs.

Acceptance criteria:

- [x] Scope decision is explicit and documented (API-key only; no JWT/OAuth surface).
- [x] Scope-expanded capabilities are intentionally absent and rejected by contract.
- [x] Proof posture reflects implemented scope (`/v1/task`, no `/task`, no batch, no tier/model fields).

Scope decisions:

- API-key authentication only in public submit path.
- Unknown submit payload fields rejected with `422`.
- Unsupported routes return `404`/`405` where not registered.

Checklist:

- [x] Decision checkpoint: API-key-only surface and no OAuth/JWT paths accepted.
- [x] Product surface:
  - no tier/model extensions,
  - no batch submit endpoint,
  - no webhook callbacks,
  - no legacy `/task` path.
- [x] Expanded tests:
  - unit model validation rejects unknown submit fields,
  - integration suite covers scope rejections and compatibility behavior,
  - scenario run adds unsupported-surface assertions.
- [x] Proof posture:
  - `make scenarios` updated to 13 scenarios,
  - scenario report includes scope gate checks.
