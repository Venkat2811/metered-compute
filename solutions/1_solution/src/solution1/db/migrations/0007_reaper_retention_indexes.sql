-- Indexes to make retention sweeps bounded and index-friendly.

CREATE INDEX IF NOT EXISTS idx_credit_transactions_created_at
  ON credit_transactions (created_at);

CREATE INDEX IF NOT EXISTS idx_credit_drift_audit_checked_at
  ON credit_drift_audit (checked_at);
