# P0-014: Tier/Model Concurrency Stress Hardening

Priority: P0
Status: done
Depends on: P0-006, P0-009, P0-012

## Objective

Strengthen production confidence with tougher JWT-tier/model integration and scenario checks beyond baseline burst tests.

## Checklist

- [x] Add integration test validating tier-based concurrency envelopes under worker pause (`pro` vs `free`)
- [x] Add integration test validating model-class cost impact on credit deduction (`small` vs `large`)
- [x] Extend scenario harness with JWT tier/model stress scenario
- [x] Ensure new checks are stable under `make prove`

## Acceptance Criteria

- [x] Concurrent submit limits differ by tier exactly as configured
- [x] Credit deductions align with model cost multipliers
- [x] Scenario harness reports pass for new stress scenario in full-check output
