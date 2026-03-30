# P0-006: Stream Worker Consumer Group and PEL Recovery

Priority: P0
Status: done
Depends on: P0-004, P0-005

## Objective

Replace Celery worker model with Redis Streams consumers and robust recovery behavior.

## Checklist

- [x] Implement worker bootstrap with consumer-group initialization
- [x] Implement `XREADGROUP` processing loop and `XACK` lifecycle
- [x] Enforce guarded state transitions for task status (`PENDING->RUNNING->TERMINAL`)
- [x] Wire tier envelopes and model-class cost factors in submit/worker paths
- [x] Implement model-class simulation behavior (`small`, `medium`, `large`) per RFC assumptions
- [x] Implement PEL recovery (`XPENDING`, `XCLAIM`/`XAUTOCLAIM`) with retry/timeout policy
- [x] Add graceful shutdown and SIGTERM handling

## Acceptance Criteria

- [x] Worker processes stream entries idempotently
- [x] Stuck entries are recoverable and test-backed
- [x] Failure paths refund/decrement behavior is correct and audited
- [x] Tier/model-class behavior is contract-tested (cost/concurrency/runtime impact)
