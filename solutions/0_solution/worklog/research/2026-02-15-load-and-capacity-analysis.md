# 2026-02-15 Load and Capacity Analysis (Solution 0)

Scope:

- `scripts/load_harness.py`
- `scripts/capacity_model.py`
- Evidence outputs under `worklog/evidence/load/`

## Method

- Deterministic seeded runs (`seed=42`), API base `http://localhost:8000`
- State reset between profiles to avoid cross-profile contamination
- For each accepted task, poll until terminal status to compute end-to-end throughput
- Profiles:
  - low: `24 requests`, `concurrency=4`
  - medium: `60 requests`, `concurrency=8`
  - high: `120 requests`, `concurrency=12`
- Stress scenarios:
  - paused worker overload
  - idempotency race (same idempotency key, concurrent submits)
  - insufficient credits
  - Redis transient outage and recovery

## Latest measured results

Source: `../evidence/load/latest-load-report.json`

- low: `accepted=6`, `429=18`, `throughput=0.4878 rps`, terminal `COMPLETED=6`
- medium: `accepted=6`, `429=54`, `throughput=0.4891 rps`, terminal `COMPLETED=6`
- high: `accepted=6`, `429=114`, `throughput=0.4880 rps`, terminal `COMPLETED=6`
- overload (worker paused): `accepted=3`, `429=117`, terminal `TIMEOUT=3`
- idempotency race: `201=1`, `200=19`, `500=0`
- insufficient credits: `402` observed
- Redis transient: degraded `503` then recovered in ~`12.851s`

## Capacity projection input

Source: `../evidence/load/latest-capacity-model.json`
Assumptions:

- utilization factor `0.7`
- polls per task `3.0`
- month seconds `2,592,000`

Sustained projection (observed):

- low: ~`885,064 tasks/month`, ~`2,655,193 polls/month`
- medium: ~`887,423 tasks/month`, ~`2,662,269 polls/month`
- high: ~`885,427 tasks/month`, ~`2,656,282 polls/month`

## Notes

- Throughput is constrained by current per-user concurrency (`MAX_CONCURRENT=3`) and worker simulation behavior.
- Submit-side reject rates (`429`) are expected under bursty open-loop load and provide a safe boundary signal.
- This track intentionally prioritizes correctness and bounded behavior over maximizing throughput.
