# BK-003: Production HA Packaging (Postgres + Redis)

Priority: Backlog
Status: done
Depends on: P0-008

## Objective

Document production packaging path beyond single-node compose: Postgres replication, Redis Sentinel/Cluster, and service-level failover playbooks.

## Checklist

- [x] Postgres HA topology proposal (replica + failover)
- [x] Redis Sentinel/Cluster topology proposal
- [x] Backup/restore and PITR playbook
- [x] Failure drills and recovery SLO definitions

## Exit Criteria

- [x] HA plan is concrete and implementation-ready for post-release evolution

## Evidence

- Plan: `../../research/2026-02-15-production-ha-packaging.md`
