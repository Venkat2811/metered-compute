-- Solution 4: Minimal schema — billing state lives in TigerBeetle
-- Only 3 tables: users, api_keys, tasks

BEGIN;

CREATE TABLE users (
    user_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name      VARCHAR(128) NOT NULL,
    credits   INT NOT NULL DEFAULT 0,          -- read-only mirror of TB balance
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    api_key   VARCHAR(128) PRIMARY KEY,
    user_id   UUID NOT NULL REFERENCES users(user_id),
    is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE tasks (
    task_id         UUID PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(user_id),
    status          VARCHAR(24) NOT NULL DEFAULT 'PENDING',
    x               INT NOT NULL,
    y               INT NOT NULL,
    result          JSONB,
    cost            INT NOT NULL,
    tb_transfer_id  VARCHAR(32) NOT NULL, -- hex of u128 TB transfer ID
    idempotency_key VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX ux_task_user_idem ON tasks(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX ix_tasks_user_status ON tasks(user_id, status);

COMMIT;
