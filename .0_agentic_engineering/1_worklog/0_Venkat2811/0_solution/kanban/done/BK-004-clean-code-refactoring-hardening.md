# BK-004: Clean Code Refactoring Hardening

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Raise codebase maintainability to production-grade quality for the RFC scope with explicit refactoring, complexity controls, and clearer boundaries.

## Checklist

- [x] Add module-boundary map (API orchestration vs domain services vs repository vs worker/reaper runtime)
- [x] Refactor large handlers (`submit`, `poll`, `cancel`, `admin`) into smaller composable units where practical for Solution 0 scope
- [x] Enforce complexity thresholds (cyclomatic complexity and function size) in CI
- [x] Remove duplicate logic across API/worker/reaper compensation paths where identified
- [x] Add architectural comments where non-obvious invariants are encoded
- [x] Run dead-code and stale-path cleanup pass

## Exit Criteria

- [x] No unbounded complexity drift in critical paths (explicit gate + documented overrides)
- [x] Critical workflows are readable end-to-end without hidden coupling
- [x] Refactor passes all existing functional and fault tests with no regressions

## Evidence

- Boundary map: `../../research/2026-02-15-module-boundary-map.md`
- Complexity gate: `scripts/complexity_gate.py`
- Gate output: `../../baselines/latest-complexity-gate.json`
