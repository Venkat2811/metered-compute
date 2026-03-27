CREATE TABLE IF NOT EXISTS cmd.webhook_dead_letters (
  event_id UUID PRIMARY KEY,
  task_id UUID NOT NULL REFERENCES cmd.task_commands(task_id) ON DELETE CASCADE,
  topic VARCHAR(128) NOT NULL,
  callback_url TEXT NOT NULL,
  payload JSONB NOT NULL,
  attempts INT NOT NULL CHECK (attempts >= 1),
  last_error TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_dead_letters_created_at
  ON cmd.webhook_dead_letters (created_at DESC);
