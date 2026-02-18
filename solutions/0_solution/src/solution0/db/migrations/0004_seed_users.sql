INSERT INTO users (name, api_key, credits, role)
VALUES
  ('{{ADMIN_NAME}}', '{{ADMIN_API_KEY}}', {{ADMIN_DEFAULT_CREDITS}}, '{{ADMIN_ROLE}}'),
  ('{{ALICE_NAME}}', '{{ALICE_API_KEY}}', {{TEST_USER1_DEFAULT_CREDITS}}, '{{USER_ROLE}}'),
  ('{{BOB_NAME}}', '{{BOB_API_KEY}}', {{TEST_USER2_DEFAULT_CREDITS}}, '{{USER_ROLE}}')
ON CONFLICT (api_key) DO UPDATE SET
  name = EXCLUDED.name,
  credits = EXCLUDED.credits,
  role = EXCLUDED.role,
  updated_at = now();
