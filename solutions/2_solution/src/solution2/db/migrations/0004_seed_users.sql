INSERT INTO users (user_id, name, api_key, credits, role)
VALUES
  ('{{ADMIN_USER_ID}}', '{{ADMIN_NAME}}', '{{ADMIN_API_KEY}}', {{ADMIN_DEFAULT_CREDITS}}, '{{ADMIN_ROLE}}'),
  ('{{TEST_USER1_USER_ID}}', '{{ALICE_NAME}}', '{{ALICE_API_KEY}}', {{TEST_USER1_DEFAULT_CREDITS}}, '{{USER_ROLE}}'),
  ('{{TEST_USER2_USER_ID}}', '{{BOB_NAME}}', '{{BOB_API_KEY}}', {{TEST_USER2_DEFAULT_CREDITS}}, '{{USER_ROLE}}')
ON CONFLICT (api_key) DO UPDATE SET
  user_id = EXCLUDED.user_id,
  name = EXCLUDED.name,
  credits = EXCLUDED.credits,
  role = EXCLUDED.role,
  updated_at = now();
