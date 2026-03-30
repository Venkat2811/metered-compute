# BK-001: Property-Based Credit Invariants and Fuzzing

Priority: P1
Status: done
Depends on: P0-010

## Objective

Add property-based tests for credit and state-transition invariants under randomized interleavings.

## Checklist

- [x] Define invariants (no negative balance, no duplicate refund, valid terminal transitions)
- [x] Add Hypothesis-style property tests for submit/cancel/worker/reaper interleavings (deterministic seeds)
- [x] Integrate with CI as non-flaky deterministic profile

## Acceptance Criteria

- [x] Invariants hold across randomized scenarios
- [x] Failures produce minimal reproducible traces
