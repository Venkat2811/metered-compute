CREATE TABLE IF NOT EXISTS cmd.inbox_events (
  event_id UUID PRIMARY KEY,
  consumer_name VARCHAR(64) NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
