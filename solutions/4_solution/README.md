# 4_solution

Name: Production Launch

Approach:

- Sol 1 Redis-native admission gate (zero-PG hot path)
- Sol 2 outbox pattern for post-admission flows (eliminates dual-write bugs)
- Kubernetes-native deployment with full HA
- Minimal infrastructure delta over Sol 1 (one container, one table)

Primary RFC:

- `../0_1_rfcs/RFC-0004-4-solution-production-launch/README.md`
