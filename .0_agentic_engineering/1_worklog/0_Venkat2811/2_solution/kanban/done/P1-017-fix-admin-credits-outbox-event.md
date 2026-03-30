# P1-017: Fix admin credits — add outbox event

Priority: P1
Status: done
Depends on: P1-008

## Objective

Add outbox event emission to the admin credits endpoint so credit adjustments are propagated through the event bus per RFC-0002.

## Why

Current admin_routes.py does a direct PG update + Redis cache sync + `credits:dirty` set add, but never emits an outbox event. Per RFC-0002, all state mutations should publish events via the outbox for downstream consumers (audit, webhooks, projector awareness). The `credits:dirty` pattern is Sol 1.

## Scope

- `src/solution2/api/admin_routes.py`
  - After credit update in PG transaction, INSERT `cmd.outbox_events` with:
    - `event_type`: `credits.adjusted`
    - `aggregate_id`: user_id
    - `routing_key`: `admin.credits.adjusted`
    - `payload`: `{user_id, old_credits, new_credits, delta, admin_id}`
  - Remove `credits:dirty` set usage (Sol 1 pattern)
  - Keep Redis cache update (write-through for read path)

## Checklist

- [x] Outbox event emitted in same PG transaction as credit update
- [x] Event payload includes old/new/delta for audit trail
- [x] No `credits:dirty` set usage
- [x] Tests verify outbox call in admin route path

## Validation

- `uv run pytest tests/unit/test_admin_routes.py -q`
- `uv run pytest tests/integration/test_admin_credits.py -q`
