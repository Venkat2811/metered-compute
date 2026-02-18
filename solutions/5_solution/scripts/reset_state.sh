#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

# Default credentials matching migrations/0002_seed.sql
alice_id="a0000000-0000-0000-0000-000000000001"
bob_id="b0000000-0000-0000-0000-000000000002"
alice_credits="${RESET_ALICE_CREDITS:-1000}"
bob_credits="${RESET_BOB_CREDITS:-500}"

# ── Flush Redis ──
docker compose exec -T redis redis-cli FLUSHALL >/dev/null

# ── Reset Postgres tasks ──
docker compose exec -T postgres psql -U postgres -d bfl >/dev/null <<SQL
TRUNCATE TABLE tasks;
UPDATE users SET credits = CASE user_id
    WHEN '${alice_id}'::uuid THEN ${alice_credits}
    WHEN '${bob_id}'::uuid THEN ${bob_credits}
    ELSE credits
END, created_at = now();
SQL

# ── Note: TigerBeetle state cannot be reset without cluster re-format.
# TB accounts persist across resets. For scenario isolation, each scenario
# should topup credits to a known value before running.

echo "state reset: redis flushed, tasks truncated, user credits restored"
