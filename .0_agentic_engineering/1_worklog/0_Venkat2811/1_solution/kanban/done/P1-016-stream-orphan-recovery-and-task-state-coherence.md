# P1-016: Stream Orphan Recovery and Task-State Coherence

Priority: P1
Status: done
Depends on: P0-006, P0-007

## Objective

Eliminate poison-message retries when stream entries outlive task persistence and tighten coherence between stream, pending markers, Redis task hash, and Postgres rows.

## Checklist

- [x] Handle "stream message exists, PG task row missing" with explicit branching:
  - [x] Retry while pending marker exists
  - [x] Ack and drop orphan entries when pending marker is absent and message exceeded grace timeout
- [x] Clear stale Redis `task:{id}` hash in orphan/drop path when safe
- [x] Ensure API persist-failure compensation removes any Redis task-state artifacts created by admission
- [x] Keep credit accounting invariant intact (no extra debit/refund)
- [x] Add unit tests for missing-row + pending-marker-present vs missing-marker paths
- [x] Add fault coverage for persist-failure artifact cleanup and validate integration gates remain green

## Acceptance Criteria

- [x] Stream worker does not indefinitely reprocess orphaned messages
- [x] No double refund or leaked active slot in orphan scenarios
- [x] Existing worker/reaper recovery tests remain green
