CREATE TABLE IF NOT EXISTS cmd.credit_reservations (
  reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id UUID NOT NULL UNIQUE,
  user_id UUID NOT NULL,
  amount INT NOT NULL,
  state VARCHAR(16) NOT NULL DEFAULT 'RESERVED' CHECK (
    state IN ('RESERVED', 'CAPTURED', 'RELEASED')
  ),
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (task_id) REFERENCES cmd.task_commands(task_id),
  FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_reservations_state_expires
  ON cmd.credit_reservations (state, expires_at);
