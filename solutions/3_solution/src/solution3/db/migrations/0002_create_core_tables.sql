CREATE TABLE IF NOT EXISTS cmd.users (
  user_id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  role VARCHAR(16) NOT NULL DEFAULT '{{DEFAULT_USER_ROLE}}' CHECK (
    role IN ({{USER_ROLE_VALUES_SQL}})
  ),
  tier VARCHAR(32) NOT NULL DEFAULT '{{DEFAULT_TIER}}' CHECK (
    tier IN ({{TIER_VALUES_SQL}})
  ),
  initial_credits INT NOT NULL CHECK (initial_credits >= 0),
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cmd.api_keys (
  key_hash CHAR(64) PRIMARY KEY,
  key_prefix VARCHAR(8) NOT NULL,
  user_id UUID NOT NULL REFERENCES cmd.users(user_id) ON DELETE CASCADE,
  role VARCHAR(16) NOT NULL CHECK (
    role IN ({{USER_ROLE_VALUES_SQL}})
  ),
  tier VARCHAR(32) NOT NULL CHECK (
    tier IN ({{TIER_VALUES_SQL}})
  ),
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cmd.task_commands (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES cmd.users(user_id),
  tier VARCHAR(32) NOT NULL CHECK (
    tier IN ({{TIER_VALUES_SQL}})
  ),
  mode VARCHAR(16) NOT NULL DEFAULT '{{DEFAULT_REQUEST_MODE}}' CHECK (
    mode IN ({{REQUEST_MODE_VALUES_SQL}})
  ),
  model_class VARCHAR(16) NOT NULL DEFAULT '{{DEFAULT_MODEL_CLASS}}' CHECK (
    model_class IN ({{MODEL_CLASS_VALUES_SQL}})
  ),
  status VARCHAR(24) NOT NULL DEFAULT '{{DEFAULT_TASK_STATUS}}' CHECK (
    status IN ({{TASK_STATUS_VALUES_SQL}})
  ),
  billing_state VARCHAR(24) NOT NULL DEFAULT '{{DEFAULT_BILLING_STATE}}' CHECK (
    billing_state IN ({{BILLING_STATE_VALUES_SQL}})
  ),
  x INT NOT NULL,
  y INT NOT NULL,
  cost INT NOT NULL CHECK (cost >= 0),
  tb_pending_transfer_id UUID NOT NULL,
  callback_url TEXT,
  idempotency_key VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_task_cmd_user_idem
  ON cmd.task_commands (user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_cmd_billing_state
  ON cmd.task_commands (billing_state, created_at)
  WHERE billing_state = 'RESERVED';

CREATE INDEX IF NOT EXISTS idx_task_cmd_user_created
  ON cmd.task_commands (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cmd.outbox_events (
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregate_id UUID NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  topic VARCHAR(128) NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_outbox_unpublished
  ON cmd.outbox_events (published_at)
  WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS cmd.inbox_events (
  event_id UUID NOT NULL,
  consumer_name VARCHAR(64) NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, consumer_name)
);

CREATE TABLE IF NOT EXISTS query.task_query_view (
  task_id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  tier VARCHAR(32) NOT NULL CHECK (
    tier IN ({{TIER_VALUES_SQL}})
  ),
  mode VARCHAR(16) NOT NULL CHECK (
    mode IN ({{REQUEST_MODE_VALUES_SQL}})
  ),
  model_class VARCHAR(16) NOT NULL CHECK (
    model_class IN ({{MODEL_CLASS_VALUES_SQL}})
  ),
  status VARCHAR(24) NOT NULL CHECK (
    status IN ({{TASK_STATUS_VALUES_SQL}})
  ),
  billing_state VARCHAR(24) NOT NULL CHECK (
    billing_state IN ({{BILLING_STATE_VALUES_SQL}})
  ),
  result JSONB,
  error TEXT,
  runtime_ms INT,
  projection_version BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_query_user_updated
  ON query.task_query_view (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS cmd.projection_checkpoints (
  projector_name VARCHAR(64) PRIMARY KEY,
  topic VARCHAR(128) NOT NULL,
  partition_id INT NOT NULL,
  committed_offset BIGINT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projection_checkpoints_topic
  ON cmd.projection_checkpoints (topic, partition_id);

CREATE TABLE IF NOT EXISTS cmd.billing_reconcile_jobs (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID NOT NULL REFERENCES cmd.task_commands(task_id) ON DELETE CASCADE,
  tb_pending_transfer_id UUID NOT NULL,
  state VARCHAR(24) NOT NULL DEFAULT 'PENDING',
  resolution VARCHAR(24),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reconcile_state
  ON cmd.billing_reconcile_jobs (state, created_at)
  WHERE state = 'PENDING';
