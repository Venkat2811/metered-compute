# 1_solution

Hardened version of `0_solution`:
- Hash-key auth model with Redis auth cache
- Transactional outbox and idempotency table
- Same Redis + Celery queue, stronger schema hygiene

Primary design doc:
- `../0_0_rfcs/RFC-0002-1-solution-celery-redis-postgres.md`
