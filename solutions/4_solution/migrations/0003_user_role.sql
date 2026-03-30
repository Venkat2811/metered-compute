-- Add user role field for admin / user authorization checks.
-- Keeps existing seeded users compatible with default role.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'user';

UPDATE users
SET role = 'admin'
WHERE user_id = 'a0000000-0000-0000-0000-000000000001'::uuid;

COMMIT;
