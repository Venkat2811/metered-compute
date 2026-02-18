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
admin_user_id="${OAUTH_ADMIN_USER_ID:-}"
user1_user_id="${OAUTH_USER1_USER_ID:-}"
user2_user_id="${OAUTH_USER2_USER_ID:-}"

if [[ -z "${admin_api_key}" || -z "${user1_api_key}" || -z "${user2_api_key}" ]]; then
  echo "reset_state requires ADMIN_API_KEY, ALICE_API_KEY, BOB_API_KEY" >&2
  exit 1
fi

if [[ -z "${admin_user_id}" || -z "${user1_user_id}" || -z "${user2_user_id}" ]]; then
  echo "reset_state requires OAUTH_ADMIN_USER_ID, OAUTH_USER1_USER_ID, OAUTH_USER2_USER_ID" >&2
  exit 1
fi

admin_reset_credits="${RESET_ADMIN_CREDITS:-1000000}"
user1_reset_credits="${RESET_USER1_CREDITS:-100}"
user2_reset_credits="${RESET_USER2_CREDITS:-250}"

docker compose exec -T redis redis-cli -n 0 FLUSHDB >/dev/null
docker compose exec -T redis redis-cli -n 1 FLUSHDB >/dev/null
docker compose exec -T redis redis-cli -n 2 FLUSHDB >/dev/null

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null <<SQL
TRUNCATE TABLE webhook_delivery_dead_letters, webhook_subscriptions, tasks, credit_transactions, credit_snapshots, token_revocations;
TRUNCATE TABLE api_keys;
UPDATE users
SET user_id = CASE api_key
    WHEN '${admin_api_key}' THEN '${admin_user_id}'::uuid
    WHEN '${user1_api_key}' THEN '${user1_user_id}'::uuid
    WHEN '${user2_api_key}' THEN '${user2_user_id}'::uuid
    ELSE user_id
END,
credits = CASE api_key
    WHEN '${admin_api_key}' THEN ${admin_reset_credits}
    WHEN '${user1_api_key}' THEN ${user1_reset_credits}
    WHEN '${user2_api_key}' THEN ${user2_reset_credits}
    ELSE credits
END,
updated_at = now();

INSERT INTO api_keys (key_hash, key_prefix, user_id, role, tier, is_active)
SELECT
  encode(digest(users.api_key, 'sha256'), 'hex') AS key_hash,
  left(users.api_key, 8) AS key_prefix,
  users.user_id,
  users.role,
  users.tier,
  users.is_active
FROM users
ON CONFLICT (key_hash) DO UPDATE SET
  key_prefix = EXCLUDED.key_prefix,
  user_id = EXCLUDED.user_id,
  role = EXCLUDED.role,
  tier = EXCLUDED.tier,
  is_active = EXCLUDED.is_active;
SQL

worker_heartbeat_key="${STREAM_WORKER_HEARTBEAT_KEY:-workers:stream:last_seen}"
for _ in $(seq 1 30); do
  heartbeat="$(docker compose exec -T redis redis-cli -n 0 GET "${worker_heartbeat_key}" | tr -d '\r')"
  if [[ -n "${heartbeat}" && "${heartbeat}" != "(nil)" ]]; then
    break
  fi
  sleep 1
done
