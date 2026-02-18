CREATE TABLE IF NOT EXISTS webhook_subscriptions (
  user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
  callback_url TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (length(callback_url) BETWEEN 1 AND 2048)
);

CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_enabled
  ON webhook_subscriptions(enabled);

CREATE TABLE IF NOT EXISTS webhook_delivery_dead_letters (
  dead_letter_id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
  event_payload JSONB NOT NULL,
  last_error TEXT NOT NULL,
  failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_dead_letters_failed_at
  ON webhook_delivery_dead_letters(failed_at DESC);
