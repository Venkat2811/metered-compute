CREATE TABLE IF NOT EXISTS query.task_query_view (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL,
  mode VARCHAR(16) NOT NULL,
  model_class VARCHAR(16) NOT NULL,
  status VARCHAR(24) NOT NULL,
  result JSONB,
  error TEXT,
  queue_name VARCHAR(32),
  runtime_ms INT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_query_user_updated
  ON query.task_query_view (user_id, updated_at DESC);
