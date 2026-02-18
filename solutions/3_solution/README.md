# 3_solution

Name: Financial Core

Approach:

- TigerBeetle pending/post/void credit lifecycle (Jepsen-verified)
- Redpanda replayable event backbone (Kafka API compatible)
- CQRS projections rebuildable from event log
- Redis as query cache layer
- ClickHouse for business event OLAP analytics
- Reconciler for stale pending transfers

Primary RFC:

- `../0_1_rfcs/RFC-0003-3-solution-financial-core/README.md`
