ALTER TABLE users
  ADD COLUMN IF NOT EXISTS tier VARCHAR(32) NOT NULL DEFAULT '{{DEFAULT_TIER}}' CHECK (
    tier IN ({{TIER_VALUES_SQL}})
  ),
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

UPDATE users
SET tier = '{{ADMIN_TIER}}'
WHERE role = '{{ADMIN_ROLE}}' AND tier <> '{{ADMIN_TIER}}';

CREATE TABLE IF NOT EXISTS api_keys (
  key_hash CHAR(64) PRIMARY KEY,
  key_prefix VARCHAR(16) NOT NULL,
  user_id UUID NOT NULL REFERENCES users(user_id),
  role VARCHAR(32) NOT NULL DEFAULT '{{DEFAULT_USER_ROLE}}' CHECK (
    role IN ({{USER_ROLE_VALUES_SQL}})
  ),
  tier VARCHAR(32) NOT NULL DEFAULT '{{DEFAULT_TIER}}' CHECK (
    tier IN ({{TIER_VALUES_SQL}})
  ),
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);

INSERT INTO api_keys (key_hash, key_prefix, user_id, role, tier, is_active)
SELECT
  encode(digest(users.api_key, 'sha256'), 'hex') AS key_hash,
  left(users.api_key, 8) AS key_prefix,
  users.user_id,
  users.role,
  users.tier,
  users.is_active
FROM users
ON CONFLICT (key_hash) DO UPDATE SET
  key_prefix = EXCLUDED.key_prefix,
  user_id = EXCLUDED.user_id,
  role = EXCLUDED.role,
  tier = EXCLUDED.tier,
  is_active = EXCLUDED.is_active;

CREATE TABLE IF NOT EXISTS credit_drift_audit (
  audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(user_id),
  redis_balance INT NOT NULL,
  db_balance INT NOT NULL,
  drift INT NOT NULL,
  action_taken VARCHAR(32),
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stream_checkpoints (
  consumer_group VARCHAR(64) PRIMARY KEY,
  last_stream_id VARCHAR(64) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_active
  ON api_keys (user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_drift_checked
  ON credit_drift_audit (checked_at DESC);
