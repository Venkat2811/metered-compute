# P1-018: Ops Hardening, Doc Consistency, and Runtime Safety

Priority: P1
Status: done
Depends on: P1-015, P1-017

## Objective

Harden runtime operational posture and align docs/matrix text to implemented behavior.

## Checklist

- [x] Add process restart policy and failure containment for reaper runtime loop
- [x] Ensure API container runs as non-root like worker/reaper
- [x] Reconcile README endpoint compatibility text (`/admin/credits` vs `/credits`)
- [x] Align RFC/matrix wording where implementation intentionally differs, without weakening guarantees
- [x] Remove or explicitly justify benchmark-only dead code paths in repository module
- [x] Validate full clean run (`make full-check`) and archive evidence snapshot

## Acceptance Criteria

- [x] Compose runtime has consistent non-root + restart behavior where expected
- [x] Docs accurately reflect shipped API/behavior
- [x] Full verification command passes from clean docker state

## Notes

- Added reaper cycle failure containment with configurable backoff (`REAPER_ERROR_BACKOFF_SECONDS`) and a unit test proving recovery after a transient cycle failure.
- Added compose restart policy (`unless-stopped`) to long-running services and enforced non-root API image runtime (`USER app`).
- Corrected docs/matrix wording:
  - compatibility endpoint text uses `/admin/credits`
  - matrix/rfc terminology now consistently references `XAUTOCLAIM`
- Retained transactional admin credit benchmark path with explicit purpose in `repository.py` and benchmark script reference (`scripts/benchmark_write_patterns.py`).
- Verification evidence:
  - `worklog/evidence/full-check-20260216T195457Z`
