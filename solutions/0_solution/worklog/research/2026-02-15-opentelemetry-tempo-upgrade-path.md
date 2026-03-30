# 2026-02-15 OpenTelemetry + Tempo Upgrade Path (Solution 0 -> 1+)

Goal:
Add distributed tracing with low-risk incremental rollout, without destabilizing Solution 0 baseline.

## Target scope

Instrument spans for:

- API submit/poll/cancel/admin handlers
- Redis Lua admission and compensation calls
- Postgres write/read operations for task + billing paths
- Celery publish and worker execution lifecycle

## Compose profile plan

Add optional `tracing` compose profile:

- `otel-collector`
- `tempo`

API/worker/reaper export OTLP to collector only when profile enabled.

## Sampling and cardinality policy

- Default sample rate: `5%` for success paths
- Always sample:
  - 5xx responses
  - compensation/refund paths
  - reaper recoveries
- Keep high-cardinality payload fields out of span attributes
- Reuse existing `trace_id` correlation id from structured logs

## Rollout

1. Add no-op instrumentation wrappers behind config flag (`OTEL_ENABLED=false` default)
2. Enable spans in local compose profile only
3. Validate overhead under load harness
4. Enable in non-prod shared environment
5. Promote to production after SLO and cost check

## Rollback

- Flip `OTEL_ENABLED=false`
- Stop tracing profile services
- Preserve existing logs/metrics dashboards as primary observability path

## Validation criteria

- No behavior regressions in API contract tests
- p95 API latency impact < 5% at baseline load
- Trace-linkability with existing JSON logs by shared correlation id
