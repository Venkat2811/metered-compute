# BK-018: High-Throughput Consistency Patterns Evaluation

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Evaluate lower-overhead consistency patterns (single-statement DML, idempotent upserts, append-only events with async projection) versus current transactional orchestration.

## Checklist

- [x] Prototype one critical path with single-statement SQL/CTE alternative
- [x] Compare correctness guarantees vs current implementation
- [x] Benchmark p50/p95 latency and throughput at medium/high concurrency
- [x] Decide preferred pattern per write path and document rationale

## Exit Criteria

- [x] Chosen write patterns are explicit and performance-justified
- [x] Correctness invariants remain intact under retries/failures
- [x] Results feed RFC evolution notes for Solution 1+

## Evidence

- Implementation: `src/solution0/db/repository.py`
- Benchmark script: `scripts/benchmark_write_patterns.py`
- Analysis: `../../research/2026-02-15-write-pattern-benchmark.md`
- Report: `../../evidence/load/latest-write-pattern-benchmark.json`
