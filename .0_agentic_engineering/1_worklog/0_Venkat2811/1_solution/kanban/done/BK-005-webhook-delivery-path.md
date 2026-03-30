# BK-005: Webhook Delivery Path

Priority: P2
Status: done
Depends on: P0-010

## Objective

Add optional webhook callback flow for task terminal events.

## Checklist

- [x] Add callback URL registration and validation
- [x] Emit terminal events to webhook dispatcher with retries/backoff
- [x] Add dead-letter handling and replay tooling

## Acceptance Criteria

- [x] Webhook delivery semantics are documented and test-covered
- [x] Failure isolation does not impact main task flow

## What changed

- API and schema surface:
  - Added `PUT/GET/DELETE /v1/webhook` in `src/solution1/api/webhook_routes.py`
  - Added request/response models in `src/solution1/models/schemas.py`
  - Added route constants and app registration wiring in `src/solution1/api/paths.py` and `src/solution1/app.py`
- Storage and migrations:
  - Added migration `0008_webhook_delivery_tables.sql`
  - Added repository functions for subscription CRUD and dead-letter persistence in `src/solution1/db/repository.py`
- Delivery engine:
  - Added webhook service helpers in `src/solution1/services/webhooks.py`
  - Added dispatcher worker with retry/backoff + DLQ in `src/solution1/workers/webhook_dispatcher.py`
  - Emitted terminal events from task cancel + stream worker completion/failure paths
- Operations:
  - Added compose service `webhook-dispatcher` (`compose.yaml`)
  - Added Prometheus scrape target + alerts for dispatcher/DLQ
  - Added replay tool `scripts/replay_webhook_dlq.py` and `make webhook-replay`
  - Added README runbook section for webhook registration and DLQ replay

## Validation run

- `ruff check src tests`
- `mypy --strict src tests`
- `pytest -q tests/unit/test_webhook_service.py tests/unit/test_webhook_dispatcher.py tests/unit/test_app_paths.py tests/unit/test_stream_worker.py tests/unit/test_reaper_paths.py tests/unit/test_reaper_recovery.py tests/unit/test_reaper_retention.py tests/unit/test_migrations.py tests/unit/test_observability_configs.py tests/unit/test_settings.py`
