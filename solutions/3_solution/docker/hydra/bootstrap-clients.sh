#!/usr/bin/env sh
set -eu

admin_endpoint="${HYDRA_ADMIN_URL:-http://hydra:4445}"

create_client() {
  client_id="$1"
  client_secret="$2"
  role="$3"
  tier="$4"

  hydra delete oauth2-client "$client_id" -e "$admin_endpoint" -q >/dev/null 2>&1 || true

  hydra create oauth2-client \
    -e "$admin_endpoint" \
    --id "$client_id" \
    --secret "$client_secret" \
    --name "$client_id" \
    --grant-type client_credentials \
    --response-type token \
    --scope task:submit,task:poll,task:cancel,admin:credits \
    --token-endpoint-auth-method client_secret_post \
    --access-token-strategy jwt \
    --metadata "{\"role\":\"$role\",\"tier\":\"$tier\"}" \
    --skip-consent \
    -q
}

create_client "$OAUTH_ADMIN_CLIENT_ID" "$OAUTH_ADMIN_CLIENT_SECRET" "admin" "${OAUTH_ADMIN_TIER:-enterprise}"
create_client "$OAUTH_USER1_CLIENT_ID" "$OAUTH_USER1_CLIENT_SECRET" "user" "${OAUTH_USER1_TIER:-pro}"
create_client "$OAUTH_USER2_CLIENT_ID" "$OAUTH_USER2_CLIENT_SECRET" "user" "${OAUTH_USER2_TIER:-free}"

echo "Hydra OAuth clients initialized"
