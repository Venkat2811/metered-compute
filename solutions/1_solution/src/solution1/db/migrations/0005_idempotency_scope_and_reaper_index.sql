DROP INDEX IF EXISTS ux_tasks_idempotency_key;

CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_user_idempotency_key
  ON tasks (user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_running_started_at
  ON tasks (started_at)
  WHERE status = 'RUNNING';
