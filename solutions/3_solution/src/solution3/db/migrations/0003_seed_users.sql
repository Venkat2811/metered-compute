INSERT INTO cmd.users (user_id, name, role, tier, initial_credits, is_active)
VALUES
  ('{{ADMIN_USER_ID}}', '{{ADMIN_NAME}}', '{{ADMIN_ROLE}}', '{{ADMIN_TIER}}', {{ADMIN_DEFAULT_CREDITS}}, true),
  ('{{TEST_USER1_USER_ID}}', '{{ALICE_NAME}}', '{{USER_ROLE}}', '{{DEFAULT_TIER}}', {{TEST_USER1_DEFAULT_CREDITS}}, true),
  ('{{TEST_USER2_USER_ID}}', '{{BOB_NAME}}', '{{USER_ROLE}}', '{{DEFAULT_TIER}}', {{TEST_USER2_DEFAULT_CREDITS}}, true)
ON CONFLICT (user_id) DO UPDATE
  SET
    name = EXCLUDED.name,
    role = EXCLUDED.role,
    tier = EXCLUDED.tier,
    initial_credits = EXCLUDED.initial_credits,
    is_active = EXCLUDED.is_active,
    updated_at = now();

INSERT INTO cmd.api_keys (key_hash, key_prefix, user_id, role, tier, is_active)
VALUES
  (
    encode(digest('{{ADMIN_API_KEY}}', 'sha256'), 'hex'),
    left('{{ADMIN_API_KEY}}', 8),
    '{{ADMIN_USER_ID}}',
    '{{ADMIN_ROLE}}',
    '{{ADMIN_TIER}}',
    true
  ),
  (
    encode(digest('{{ALICE_API_KEY}}', 'sha256'), 'hex'),
    left('{{ALICE_API_KEY}}', 8),
    '{{TEST_USER1_USER_ID}}',
    '{{USER_ROLE}}',
    '{{DEFAULT_TIER}}',
    true
  ),
  (
    encode(digest('{{BOB_API_KEY}}', 'sha256'), 'hex'),
    left('{{BOB_API_KEY}}', 8),
    '{{TEST_USER2_USER_ID}}',
    '{{USER_ROLE}}',
    '{{DEFAULT_TIER}}',
    true
  )
ON CONFLICT (key_hash) DO UPDATE
  SET
    key_prefix = EXCLUDED.key_prefix,
    user_id = EXCLUDED.user_id,
    role = EXCLUDED.role,
    tier = EXCLUDED.tier,
    is_active = EXCLUDED.is_active,
    updated_at = now();
