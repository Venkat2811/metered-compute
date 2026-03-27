# P1-008 Solution 3 - Correctness and Fidelity Hardening

Objective:

Resolve the remaining high-risk integrity gaps in Sol3 while preserving the Sol3 architecture (TB + Redpanda + RabbitMQ + CQRS). Do not collapse Sol3 into Sol5.

Acceptance criteria:

- [x] Keep reconciler/watchdog boundary aligned with RFC intent and remove dead runtime surface that is not used.
- [x] Fix cancel correctness path to avoid TB-first false negatives and improve client response semantics.
- [x] Ensure all admin top-up flows are audit-complete in the same failure domain as credit writes, or clearly document best-effort behavior as a conscious design choice.
- [x] Remove or retire unused billing event constants/settings that are not produced.
- [x] Keep Sol3 docs aligned to final implemented behavior before resuming full implementation.

TDD order:

1. Add/adjust unit tests for cancel semantics (TB void before/after DB, terminal-task behavior).
2. Add/adjust unit tests for admin top-up audit behavior and idempotent safety or explicit best-effort documentation.
3. Add repository-level test assertions for dead constants removal effects (where applicable).
4. Implement minimal code changes in a sequence that preserves existing Sol3 invariants.
5. Update `README.md`, `worklog/kanban/BOARD.md`, and any RFC-facing notes.
6. Re-queue this card only after tests and proof command are green on changed scope.

Checklist:

- [x] `api/task_write_routes.py`
  - [x] Decide final cancel order policy and implement: DB-side cancel write first, then TB void attempt.
  - [x] Add DB-first/TB-fallback unit tests with:
    - [x] already terminal tasks return `409` not `503`
    - [x] TB temporary failure does not prevent DB cancel when task is still eligible
    - [x] idempotent behavior for repeated cancel attempts

- [x] `workers/watchdog.py` + `compose.yaml` + `core/settings.py`
  - [x] Delete watchdog runtime if not needed for Sol3 operating model.
  - [x] Remove `watchdog` service from compose.
  - [x] Remove `watchdog_metrics_port` from settings and README references.
  - [x] Add explicit regression test for compose service absence or remove related test surface.

- [x] `api/admin_routes.py` + `db/repository.py`
  - [x] Choose one mode for top-up audit:
    - [ ] mode A: best-effort audit event and explicit Sol3 docs statement
    - [x] mode B: fail-hard on outbox mismatch with idempotent retry safety
  - [x] Ensure `transfer_id` reuse between TigerBeetle and outbox record when fail-hard is selected.
  - [x] Add regression test that validates chosen mode under top-up + outbox failure.

- [x] `constants.py` + `core/settings.py` + docs
  - [x] Resolve `billing.captured` / `billing.released` dead pair:
    - [ ] implement `billing.captured|billing.released` production events, or
    - [x] remove constants/settings and update RFC/docs/contracts accordingly
  - [x] Ensure `TASK_EVENT_TYPES` only contains emitted types.

- [x] `README.md` + `RFC-0003` (or solution-local RFC notes)
- [x] Update “known limitation” section:
    - [x] TB capture before DB finalize can still lead to completed-without-result on exceptional requeue/ack edge
    - [x] document that this is architectural tradeoff vs Sol5
  - [x] Update any claims around watchdog/reconciler and admin audit semantics.

Completion criteria:

- [x] All checkboxed checks above are marked complete by code and tests.
- [x] `BOARD.md` Planned Tasks list includes `P1-008-solution3-rfc-hardening-and-integrity.md`.
- [x] No remaining ambiguity between Sol3 scope and implementation.

Implementation notes:

- Do not treat this as a Sol3 architecture rewrite.
- Reconcile only the integrity gaps and correctness clarity identified above.
- Keep Sol3 complexity where mandated by RFC-0003 (projector, dispatcher, reconciler role).
