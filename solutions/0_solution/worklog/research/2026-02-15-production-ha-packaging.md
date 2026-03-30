# 2026-02-15 Production HA Packaging Plan (Postgres + Redis)

Scope:
Post-assignment production packaging for Solution 0 architecture, preserving behavior while improving failure tolerance.

## Postgres topology

Proposed:

- 1 primary + 1 synchronous replica + 1 async replica
- automatic failover manager (Patroni or managed equivalent)
- WAL archiving + PITR backups

Runbook requirements:

- failover trigger and health checks
- read/write endpoint switch policy
- data-loss budget explicitly documented (RPO/RTO)

## Redis topology

Proposed:

- Redis Sentinel setup (3 sentinels) for broker/cache HA
- AOF persistence enabled for durability of runtime keys where needed

Notes:

- Celery broker failover behavior must be validated under sentinel failover drill
- idempotency and active counters are ephemeral runtime state and can be rebuilt safely

## Backup and restore

- Postgres nightly base backup + continuous WAL shipping
- quarterly restore drill to isolated environment
- Redis backup optional for forensics; not source of truth for billing ledger

## Failure drills

Minimum recurring drills:

- Postgres primary crash and controlled failover
- Redis primary crash and sentinel promotion
- worker pool restart during high queue depth
- API rolling restart under load

## SLO envelope (initial)

- API availability: `>= 99.9%`
- submit success (non-overload) `>= 99.5%`
- admin credit mutation durability: no lost committed writes
- recovery from single-node failure within 5 minutes

## Migration path

- Keep compose baseline for local/dev reproducibility
- Introduce production overlays (Helm/terraform or managed services) only after Solution 0 acceptance
