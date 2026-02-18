# BK-002: OpenTelemetry + Tempo Upgrade Path

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Prepare an explicit migration path from baseline metrics/logging to distributed tracing without destabilizing Solution 0.

## Checklist

- [x] Define trace span model for submit/poll/worker lifecycle
- [x] Add collector + Tempo compose profile design
- [x] Establish trace-cardinality and sampling policy
- [x] Add rollout and rollback steps

## Exit Criteria

- [x] Upgrade path is documented and low-risk
- [x] Instrumentation plan reuses existing correlation IDs

## Evidence

- Plan: `../../research/2026-02-15-opentelemetry-tempo-upgrade-path.md`
