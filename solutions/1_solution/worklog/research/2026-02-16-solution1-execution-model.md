# Solution 1 Execution Model (Redis-Native)

Date: 2026-02-16

## Objective

Map RFC-0001 into concrete implementation boundaries before coding begins.

## Hot Path Rules

- API submit and poll happy paths must avoid Postgres reads/writes.
- JWT validation must be local crypto verification in API process.
- Redis Lua mega-script performs admission and enqueue atomically.
- Worker consumes from Redis Streams consumer group.

## Services

- `api`: public endpoints and JWT auth enforcement
- `oauth`: token issuance endpoint and credential validation against Postgres
- `worker`: stream consumer and task execution simulator
- `reconciler`: snapshots, drift audit, and recovery loops

## Data and key contracts

- Redis working set:
  - `credits:{user_id}`
  - `idem:{user_id}:{idempotency_key}`
  - `active:{user_id}`
  - `task:{task_id}`
  - `tasks:stream`
  - `credits:dirty`
- Postgres control plane:
  - `users`, `api_keys`
  - `credit_transactions`, `credit_snapshots`, `credit_drift_audit`

## Correctness invariants

- Credits never go negative.
- Idempotency is scoped per user in both Redis keys and Postgres uniqueness constraints.
- Task terminal transitions are single-writer safe with guarded updates.
- Refund/decrement operations only execute when transition ownership is confirmed.

## Risk concentration and mitigation

- Redis availability is critical for hot path: mitigate with readiness checks, alerts, and controlled 503 behavior.
- Redis/Postgres durability gap exists by design in this track: mitigate with periodic snapshot flush + drift audit.
- Stream stuck entries: mitigate with PEL scan and claim workflow.
