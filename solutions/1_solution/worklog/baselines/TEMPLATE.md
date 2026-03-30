# Solution 1 Baseline Record Template

Status: draft baseline
Date:
Commit:
Environment: local / CI

## Environment

- Python:
- Docker:
- Docker Compose:
- Host OS:

## Dependency Snapshot

- lockfile hash:
- core stack versions:

## Functional Results

- OAuth token issuance verified:
- submit/poll success rate:
- auth failure behavior verified:
- insufficient credit behavior verified:
- admin top-up behavior verified:
- idempotency behavior verified:

## Reliability Results

- worker failure + PEL recovery behavior:
- stream backlog recovery behavior:
- snapshot/drift reconciler behavior:

## Performance Snapshot

- submit p50/p95:
- poll p50/p95:
- stream lag peak:
- pending entries peak:

## Observability Verification

- `/metrics` exposed: yes/no
- Prometheus scrape healthy: yes/no
- Grafana dashboard loaded: yes/no
- log keys present (`task_id`, `user_id`, `trace_id`): yes/no

## Gate Outcome

- Unit gate:
- Integration gate:
- Release gate:

## Notes

- Known risks:
- Follow-up cards:
