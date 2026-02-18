# P1-030: RFC-0001 Folder Restructure and Doc Reconciliation

Priority: P1
Status: done
Depends on: P1-027, P1-022, P1-021

## Objective

Restructure RFC-0001 into folder format (same treatment as RFC-0000) and reconcile docs against post-fix code in one pass.

## Checklist

- [x] Split RFC-0001 into folder docs (`README.md`, `request-flows.md`, `data-ownership.md`, `capacity-model.md`)
- [x] Reconcile claims with implemented behavior after P1-027/P1-022/P1-021
- [x] Update matrix/readme references where wording/paths changed
- [x] Ensure no duplicated or conflicting statements across RFC, matrix, and assumptions docs

## Acceptance Criteria

- [x] Reviewer can navigate RFC-0001 in folder format with clear separation of concerns
- [x] Code-vs-doc mismatches for solution 1 are resolved in a single reconciliation pass

## Notes

- Replaced single-file RFC with folder layout:
  - `../../0_1_rfcs/RFC-0001-1-solution-redis-native-engine/README.md`
  - `../../0_1_rfcs/RFC-0001-1-solution-redis-native-engine/request-flows.md`
  - `../../0_1_rfcs/RFC-0001-1-solution-redis-native-engine/data-ownership.md`
  - `../../0_1_rfcs/RFC-0001-1-solution-redis-native-engine/capacity-model.md`
- Reconciled documented behavior with shipped code:
  - submit response includes `estimated_seconds`
  - model multiplier for `large` is `5`
  - worker includes explicit one-time 10s warmup
  - JWT scope enforcement is route-specific (`task:submit`, `task:poll`, `task:cancel`, `admin:credits`)
  - revocation key scheme is day-sharded with TTL
- Updated references in:
  - `../../README.md` (solution-local readme reference)
  - `RUNBOOK.md`
  - `kanban/BOARD.md`
- Verification evidence:
  - `../evidence/full-check-20260216T221946Z/`
