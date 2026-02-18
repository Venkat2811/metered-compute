CREATE TABLE IF NOT EXISTS cmd.task_commands (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'async',
  model_class VARCHAR(16) NOT NULL DEFAULT 'small',
  status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
  x INT NOT NULL,
  y INT NOT NULL,
  cost INT NOT NULL,
  callback_url TEXT,
  idempotency_key VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_task_cmd_user_idem
  ON cmd.task_commands (user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_cmd_status_created
  ON cmd.task_commands (status, created_at);

CREATE INDEX IF NOT EXISTS idx_task_cmd_user_created
  ON cmd.task_commands (user_id, created_at DESC);
