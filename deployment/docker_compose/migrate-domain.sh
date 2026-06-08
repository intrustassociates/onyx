#!/usr/bin/env bash
# Migrate an existing Onyx deployment to a new domain.
#
# Run this on the host where docker-compose is deployed (i.e. via SSH).
# Requirements:
#   - You are in `deployment/docker_compose/` of this repo on the server.
#   - `.env` already exists (the current deployment env file).
#   - DNS for the new domain already points at this host's public IP.
#   - Port 80 is reachable from the public internet (Let's Encrypt HTTP-01).
#
# Usage:
#   NEW_DOMAIN=onyx.intrustassociates.com \
#   LETSENCRYPT_EMAIL=admin@intrustassociates.com \
#   ./migrate-domain.sh
#
# Optional env vars:
#   COMPOSE_FILE         Docker Compose file (default: docker-compose.prod.yml)
#   ENV_FILE             Env file path (default: .env)
#   STAGING              "1" to use Let's Encrypt staging (test certs). Default: 0.
#   SKIP_LETSENCRYPT     "1" to skip cert issuance (use if cert already exists).
#   SKIP_RESTART         "1" to skip the docker compose restart.

set -euo pipefail

require_env() {
  local name=$1
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: \$${name} is required" >&2
    exit 1
  fi
}

require_env NEW_DOMAIN
require_env LETSENCRYPT_EMAIL

COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.prod.yml}
ENV_FILE=${ENV_FILE:-.env}
STAGING=${STAGING:-0}
SKIP_LETSENCRYPT=${SKIP_LETSENCRYPT:-0}
SKIP_RESTART=${SKIP_RESTART:-0}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file '$ENV_FILE' not found. Run from deployment/docker_compose/." >&2
  exit 1
fi
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: compose file '$COMPOSE_FILE' not found." >&2
  exit 1
fi

backup=".env.bak.$(date +%Y%m%d-%H%M%S)"
cp "$ENV_FILE" "$backup"
echo "Backed up $ENV_FILE -> $backup"

upsert_env() {
  local key=$1
  local value=$2
  if grep -q "^${key}=" "$ENV_FILE"; then
    # macOS and Linux compatible in-place sed via a temp file
    awk -v k="$key" -v v="$value" -F= '
      BEGIN { OFS="=" }
      $1 == k { print k, v; replaced=1; next }
      { print }
      END { if (!replaced) exit 0 }
    ' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
  fi
}

echo "Updating env vars in $ENV_FILE:"
echo "  WEB_DOMAIN -> https://${NEW_DOMAIN}"
echo "  DOMAIN     -> ${NEW_DOMAIN}"
upsert_env WEB_DOMAIN "https://${NEW_DOMAIN}"
upsert_env DOMAIN "${NEW_DOMAIN}"

if [[ "$SKIP_LETSENCRYPT" != "1" ]]; then
  echo
  echo "Issuing Let's Encrypt cert for ${NEW_DOMAIN}..."
  echo "  (Set STAGING=1 to dry-run with the staging CA first.)"
  DOMAIN="$NEW_DOMAIN" EMAIL="$LETSENCRYPT_EMAIL" STAGING="$STAGING" ./init-letsencrypt.sh
else
  echo "Skipping Let's Encrypt (SKIP_LETSENCRYPT=1)."
fi

if [[ "$SKIP_RESTART" != "1" ]]; then
  echo
  echo "Restarting docker compose stack..."
  docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --remove-orphans
else
  echo "Skipping restart (SKIP_RESTART=1)."
fi

echo
echo "Domain migration done. Verify with:"
echo "  curl -I https://${NEW_DOMAIN}/"
echo
echo "Post-migration manual checklist (cannot be automated):"
echo "  1. Update OAuth/OIDC/SAML redirect URIs in your identity provider to:"
echo "       https://${NEW_DOMAIN}/auth/oauth/callback"
echo "       https://${NEW_DOMAIN}/auth/oidc/callback   (if OIDC)"
echo "       https://${NEW_DOMAIN}/auth/saml/callback   (if SAML)"
echo "     Keep the old domain registered for ~2 weeks during the transition."
echo "  2. If Slack/Discord bots are configured, update their webhook URLs."
echo "  3. Roll back: restore $backup and re-run docker compose."
