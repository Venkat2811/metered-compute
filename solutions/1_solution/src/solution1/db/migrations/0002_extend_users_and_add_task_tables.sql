CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS user_id UUID DEFAULT gen_random_uuid() UNIQUE,
  ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT '{{DEFAULT_USER_ROLE}}' CHECK (
    role IN ({{USER_ROLE_VALUES_SQL}})
  ),
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS tasks (
  task_id UUID PRIMARY KEY,
  api_key CHAR(36) NOT NULL REFERENCES users(api_key),
  user_id UUID NOT NULL REFERENCES users(user_id),
  x INT NOT NULL,
  y INT NOT NULL,
  cost INT NOT NULL CHECK (cost >= 0),
  status VARCHAR(16) NOT NULL DEFAULT '{{DEFAULT_TASK_STATUS}}' CHECK (
    status IN ({{TASK_STATUS_VALUES_SQL}})
  ),
  result JSONB,
  error TEXT,
  runtime_ms INT,
  idempotency_key VARCHAR(128),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS credit_transactions (
  txn_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(user_id),
  task_id UUID,
  delta INT NOT NULL,
  reason VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS credit_snapshots (
  user_id UUID PRIMARY KEY REFERENCES users(user_id),
  balance INT NOT NULL CHECK (balance >= 0),
  snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
