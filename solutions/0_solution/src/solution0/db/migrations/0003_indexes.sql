CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_idempotency_key
  ON tasks (idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_tasks_status_created
  ON tasks (status, created_at);

CREATE INDEX IF NOT EXISTS idx_tasks_user_created
  ON tasks (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_credit_txn_user_created
  ON credit_transactions (user_id, created_at DESC);
