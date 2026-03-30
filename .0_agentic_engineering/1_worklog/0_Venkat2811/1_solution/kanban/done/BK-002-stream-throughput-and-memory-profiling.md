# BK-002: Stream Throughput and Memory Profiling

Priority: P1
Status: done
Depends on: P0-010

## Objective

Quantify Redis Streams throughput and memory behavior under sustained load, then tune consumer and trim strategy.

## Checklist

- [x] Add load profiles for producer/consumer saturation
- [x] Measure stream length growth, PEL growth, and latency percentiles
- [x] Tune `MAXLEN`/trim policy and batch consumption settings

## Acceptance Criteria

- [x] Capacity model is backed by measured data
- [x] Tuning changes are documented with before/after metrics

## What changed

- `scripts/load_harness.py`
  - Added stream observability sampling during each load profile:
    - `XLEN tasks:stream`
    - `XPENDING tasks:stream workers` summary count
    - Redis `INFO memory` (`used_memory`)
  - Added profile-level stream summaries (`start`, `end`, `max`, `p95`, `growth`) for:
    - `stream_length`
    - `pel_pending`
    - `redis_used_memory_bytes`
  - Added sustained saturation controls:
    - `--saturation-requests`
    - `--saturation-concurrency`
    - `--saturation-retry-attempts`
    - `--saturation-retry-sleep-seconds`
  - Added runtime-setting capture from live worker container env (`docker compose exec worker printenv ...`) to avoid host/env drift in reports.

- `scripts/capacity_model.py`
  - Added stream-aware capacity rows:
    - `stream_max`, `pel_max`, `redis_memory_max_mib`
  - Added baseline-vs-tuned compare mode:
    - `--compare-input <baseline-report.json>`
  - Added comparison deltas for throughput, p95, stream max, PEL max, and Redis max memory.

- New tests
  - `tests/unit/test_load_harness_stream_metrics.py`
  - `tests/unit/test_capacity_model_compare.py`

## Measured evidence

Generated artifacts:

- Baseline report: `worklog/evidence/load/bk002-baseline.json`
- Tuned report: `worklog/evidence/load/bk002-tuned.json`
- Compare JSON: `worklog/evidence/load/bk002-capacity-compare.json`
- Compare markdown: `worklog/evidence/load/bk002-capacity-compare.md`

Key saturation comparison (same load profile):

- Baseline (`read_count=1`, `claim_count=20`)
  - throughput: `0.4971 rps`
  - p95 submit latency: `20.473 ms`
  - stream max: `113`
  - PEL max: `5`
  - Redis max memory: `1.958 MiB`
- Tuned (`read_count=4`, `claim_count=64`)
  - throughput: `0.4967 rps`
  - p95 submit latency: `19.151 ms`
  - stream max: `111`
  - PEL max: `7`
  - Redis max memory: `1.954 MiB`

Outcome:

- No material throughput gain from the tested tuning pair.
- Minor latency improvement at saturation, but PEL behavior regressed slightly.
- Decision: keep baseline defaults (`read_count=1`, `claim_count=20`) and keep `REDIS_TASKS_STREAM_MAXLEN=500000` unchanged.

## Validation run

- `ruff check src scripts tests`
- `mypy --strict src tests`
- `pytest -q tests/unit/test_load_harness_stream_metrics.py tests/unit/test_capacity_model_compare.py tests/unit/test_tooling_auth_contract.py tests/unit/test_settings.py`
