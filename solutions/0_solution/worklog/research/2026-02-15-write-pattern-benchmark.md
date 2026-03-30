# 2026-02-15 Write Pattern Benchmark (Solution 0)

Scope:

- `scripts/benchmark_write_patterns.py`
- `src/solution0/db/repository.py`

Variants compared for admin credit update:

1. `single_statement` (CTE update + audit insert)
2. `transactional_two_statement` (explicit transaction with two statements)

## Why this benchmark

The admin credit update path is a high-frequency mutation candidate and directly relevant to transaction footprint and lock pressure.

## Latest results

Source: `../evidence/load/latest-write-pattern-benchmark.json`
Run params:

- iterations: `500`
- concurrency: `20`

Observed:

- `single_statement`
  - throughput: `1843.3749 ops/s`
  - p50: `5.271 ms`
  - p95: `15.494 ms`
- `transactional_two_statement`
  - throughput: `1436.5460 ops/s`
  - p50: `11.6317 ms`
  - p95: `25.8412 ms`

Winner: `single_statement`

## Decision

Use the single-statement CTE pattern for this path in Solution 0.

Rationale:

- Preserves atomicity for update + audit write
- Reduces round trips and lock hold time relative to two-statement orchestration
- Measurably lower latency and higher throughput under concurrent load
