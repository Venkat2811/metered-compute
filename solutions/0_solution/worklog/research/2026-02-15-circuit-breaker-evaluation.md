# 2026-02-15 Circuit Breaker Library Evaluation (Python 3.12)

## Question

Should Solution 0 use a library circuit breaker instead of custom code?

## Candidates reviewed

1. `pybreaker` (PyPI)

- Latest release observed: `1.4.1`
- Mature and widely used, but primary async support is not native asyncio-first for our FastAPI async call graph.
- Source: https://pypi.org/project/pybreaker/

2. `aiobreaker` (PyPI)

- Async-oriented interface, but release cadence is old (latest observed `1.2.0`, 2021).
- Source: https://pypi.org/project/aiobreaker/

3. `aioresilience` (PyPI)

- Newer async resilience toolkit (release observed in 2025), but lower maturity/adoption signal for this spec baseline.
- Source: https://pypi.org/project/aioresilience/

## Decision for Solution 0

Do not ship circuit breaker in Solution 0 hot paths.

Rationale:

- RFC0 and matrix do not require breaker semantics for this track.
- Adding breaker logic introduced extra state transitions and new failure branches without clear net benefit at this layer.
- Existing degradation controls are already deterministic and test-backed:
  - dependency readiness checks
  - bounded request handling with explicit `503`
  - Lua admission guard + compensation + reaper recovery

## Forward plan

- Keep breaker discussion as an evolution topic for Solution 1+ where routing and dependency surface area increase.
- If breaker is reintroduced later, prefer a vetted asyncio-native library with clear maintenance signal and load-test evidence.
