#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ -f "${repo_root}/.env.dev.defaults" ]]; then
  # shellcheck disable=SC1091
  source "${repo_root}/.env.dev.defaults"
fi

admin_api_key="${ADMIN_API_KEY:-}"
user1_api_key="${ALICE_API_KEY:-}"
user2_api_key="${BOB_API_KEY:-}"

if [[ -z "${admin_api_key}" || -z "${user1_api_key}" || -z "${user2_api_key}" ]]; then
  echo "reset_state requires ADMIN_API_KEY, ALICE_API_KEY, BOB_API_KEY" >&2
  exit 1
fi

admin_reset_credits="${RESET_ADMIN_CREDITS:-1000000}"
user1_reset_credits="${RESET_USER1_CREDITS:-100}"
user2_reset_credits="${RESET_USER2_CREDITS:-250}"

docker compose exec -T redis redis-cli -n 0 FLUSHDB >/dev/null
docker compose exec -T redis redis-cli -n 1 FLUSHDB >/dev/null
docker compose exec -T redis redis-cli -n 2 FLUSHDB >/dev/null

docker compose exec -T postgres psql -U postgres -d postgres >/dev/null <<SQL
TRUNCATE TABLE tasks, credit_transactions, credit_snapshots;
UPDATE users
SET credits = CASE api_key
    WHEN '${admin_api_key}' THEN ${admin_reset_credits}
    WHEN '${user1_api_key}' THEN ${user1_reset_credits}
    WHEN '${user2_api_key}' THEN ${user2_reset_credits}
    ELSE credits
END,
updated_at = now();
SQL
