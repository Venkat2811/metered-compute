# P1-017: Observability Contract (Metrics, Alerts, and Cardinality)

Priority: P1
Status: done
Depends on: P0-008

## Objective

Close RFC-0001 observability contract gaps and remove high-cardinality request labels.

## Checklist

- [x] Replace raw request path label with canonical route template label in HTTP metrics middleware
- [x] Add missing RFC metrics and wire instrumentation:
  - [x] `stream_consumer_lag`
  - [x] `stream_pending_entries`
  - [x] `jwt_validation_duration_seconds`
  - [x] `snapshot_flush_duration_seconds`
  - [x] `token_issuance_total`
  - [x] `pel_recovery_total`
- [x] Add/align Prometheus alert rules for stream lag, PEL growth, drift threshold, snapshot staleness
- [x] Scrape reaper metrics endpoint
- [x] Expand observability tests for metric and alert presence

## Acceptance Criteria

- [x] `/metrics` includes required series with stable label cardinality
- [x] Prometheus config includes RFC-aligned alert coverage
- [x] Unit tests validate config and metric registration for new series
