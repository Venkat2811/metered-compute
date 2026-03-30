-- Seed data: two users with API keys (plaintext, like Sol 0)
-- Test API keys:
--   alice: sk-alice-secret-key-001
--   bob:   sk-bob-secret-key-002

BEGIN;

INSERT INTO users (user_id, name, credits) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'alice', 1000),
    ('b0000000-0000-0000-0000-000000000002', 'bob',   500);

INSERT INTO api_keys (api_key, user_id, is_active) VALUES
    ('sk-alice-secret-key-001', 'a0000000-0000-0000-0000-000000000001', true),
    ('sk-bob-secret-key-002',   'b0000000-0000-0000-0000-000000000002', true);

COMMIT;
