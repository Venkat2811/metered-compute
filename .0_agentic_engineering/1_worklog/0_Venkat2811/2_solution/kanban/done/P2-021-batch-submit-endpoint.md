# P2-021: Batch submit endpoint

Priority: P2
Status: done

## Objective

Implement `POST /v1/task/batch` endpoint with transactional reservation semantics and RFC-0002-compatible behavior.

## Scope

- Add route handlers for `/v1/task/batch` and `/task/batch`
- Validate batch payload and enforce per-user concurrency envelope across batch
- Execute batch admission in one transactional path (single request-level decision)
- Persist pending read-model state in Redis for immediate pollability
- Return batch identifiers and accepted task IDs with aggregate cost

## Notes

- RFC-0002 mentions batch as a differentiator but not P0
- Implementation now lives in:
  - `src/solution2/api/task_write_routes.py`
  - `src/solution2/services/billing.py`
  - `src/solution2/api/contracts.py`
  - `src/solution2/models/schemas.py`

## Validation

- `make gate-unit`
- `make prove`
