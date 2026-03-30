# P0-007: Reconciler Snapshot Drift and Expiry Jobs

Priority: P0
Status: done
Depends on: P0-004, P0-006

## Objective

Implement periodic reconciler service for Redis/Postgres consistency and operational cleanup.

## Checklist

- [x] Flush `credits:dirty` to `credit_snapshots`
- [x] Run drift detection and write `credit_drift_audit`
- [x] Recover orphan/stuck task state paths not handled by workers
- [x] Apply result expiry/retention policy
- [x] Persist stream recovery checkpoints/telemetry as needed

## Acceptance Criteria

- [x] Reconciler loops are idempotent and safe under retries
- [x] Drift and snapshot behavior is observable via metrics/logs
- [x] Fault tests prove recovery behavior against injected failures
