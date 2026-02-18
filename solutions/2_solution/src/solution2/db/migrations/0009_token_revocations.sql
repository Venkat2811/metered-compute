CREATE EXTENSION IF NOT EXISTS pg_partman;

CREATE TABLE IF NOT EXISTS token_revocations (
  jti TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES users(user_id),
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (jti, revoked_at)
) PARTITION BY RANGE (revoked_at);

CREATE INDEX IF NOT EXISTS idx_token_revocations_user
  ON token_revocations (user_id, revoked_at);

CREATE INDEX IF NOT EXISTS idx_token_revocations_jti
  ON token_revocations (jti);

DO $$
DECLARE
  partman_schema TEXT;
  parent_table TEXT := 'public.token_revocations';
  part_config_exists BOOLEAN := false;
BEGIN
  SELECT namespace.nspname
  INTO partman_schema
  FROM pg_extension extension
  JOIN pg_namespace namespace ON namespace.oid = extension.extnamespace
  WHERE extension.extname = 'pg_partman';

  IF partman_schema IS NULL THEN
    RAISE EXCEPTION 'pg_partman extension is not available';
  END IF;

  EXECUTE format(
    'SELECT EXISTS (SELECT 1 FROM %I.part_config WHERE parent_table = $1)',
    partman_schema
  )
  INTO part_config_exists
  USING parent_table;

  IF NOT part_config_exists THEN
    EXECUTE format(
      'SELECT %I.create_parent(p_parent_table := %L, p_control := %L, p_interval := %L, p_premake := %s)',
      partman_schema,
      parent_table,
      'revoked_at',
      '1 day',
      2
    );
  END IF;

  EXECUTE format(
    'UPDATE %I.part_config SET retention = %L, retention_keep_table = false WHERE parent_table = %L',
    partman_schema,
    '2 days',
    parent_table
  );
END $$;
