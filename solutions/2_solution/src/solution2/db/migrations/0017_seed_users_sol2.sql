INSERT INTO users (user_id, name, api_key, credits, role, tier, is_active)
VALUES
  ('{{ADMIN_USER_ID}}', '{{ADMIN_NAME}}', '{{ADMIN_API_KEY}}', {{ADMIN_DEFAULT_CREDITS}}, '{{ADMIN_ROLE}}', '{{ADMIN_TIER}}', true),
  ('{{TEST_USER1_USER_ID}}', '{{ALICE_NAME}}', '{{ALICE_API_KEY}}', {{TEST_USER1_DEFAULT_CREDITS}}, '{{USER_ROLE}}', '{{DEFAULT_TIER}}', true),
  ('{{TEST_USER2_USER_ID}}', '{{BOB_NAME}}', '{{BOB_API_KEY}}', {{TEST_USER2_DEFAULT_CREDITS}}, '{{USER_ROLE}}', '{{DEFAULT_TIER}}', true)
ON CONFLICT (api_key) DO UPDATE
  SET
    user_id = EXCLUDED.user_id,
    name = EXCLUDED.name,
    credits = EXCLUDED.credits,
    role = EXCLUDED.role,
    tier = EXCLUDED.tier,
    is_active = EXCLUDED.is_active,
    updated_at = now();

INSERT INTO api_keys (key_hash, key_prefix, user_id, role, tier, is_active)
SELECT
  encode(digest(u.api_key, 'sha256'), 'hex') AS key_hash,
  left(u.api_key, 8) AS key_prefix,
  u.user_id,
  u.role,
  u.tier,
  u.is_active
FROM users AS u
WHERE u.api_key IN ('{{ADMIN_API_KEY}}', '{{ALICE_API_KEY}}', '{{BOB_API_KEY}}')
ON CONFLICT (key_hash) DO UPDATE
  SET
    key_prefix = EXCLUDED.key_prefix,
    user_id = EXCLUDED.user_id,
    role = EXCLUDED.role,
    tier = EXCLUDED.tier,
    is_active = EXCLUDED.is_active;
