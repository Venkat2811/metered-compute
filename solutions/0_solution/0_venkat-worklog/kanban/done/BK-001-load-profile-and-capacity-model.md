# BK-001: Load Profile and Capacity Model

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Create realistic load profiles and translate observed behavior into capacity planning inputs (`R_task`, `R_poll`, queue depth, saturation points).

## Checklist

- [x] Define low/medium/high traffic profiles
- [x] Add scripted load runs with reproducible seeds
- [x] Capture queue latency and worker utilization under load
- [x] Produce monthly capacity projection sheet from measured data

## Exit Criteria

- [x] Capacity model is evidence-backed, not assumption-only
- [x] Inputs can be reused by Solution 1+ RFC comparisons

## Evidence

- Script: `scripts/load_harness.py`
- Script: `scripts/capacity_model.py`
- Analysis: `../../research/2026-02-15-load-and-capacity-analysis.md`
- Output: `../../evidence/load/latest-load-report.json`
- Output: `../../evidence/load/latest-capacity-model.json`
- Output: `../../evidence/load/latest-capacity-model.md`
