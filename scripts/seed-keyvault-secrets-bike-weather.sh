#!/usr/bin/env bash
# Seed Azure Key Vault with all secrets required by the bike-weather deployment.
#
# Prerequisites:
#   - Azure CLI (`az`) installed and logged in
#   - Access to the homelab-timosur Key Vault
#
# Usage:
#   ./scripts/seed-keyvault-secrets.sh
#
# The script generates random passwords where needed and is idempotent —
# existing secrets are skipped (not overwritten).

set -euo pipefail

VAULT_NAME="homelab-timosur"

# ── Helpers ───────────────────────────────────────────────────────────────────

generate_password() {
  openssl rand -base64 32 | tr -d '/+=' | head -c 32
}

generate_secret_key() {
  openssl rand -base64 64 | tr -d '/+=' | head -c 64
}

set_secret() {
  local key="$1"
  local value="$2"

  # Check if secret already exists
  if az keyvault secret show --vault-name "$VAULT_NAME" --name "$key" &>/dev/null; then
    echo "  SKIP  $key (already exists)"
  else
    az keyvault secret set --vault-name "$VAULT_NAME" --name "$key" --value "$value" --output none
    echo "  SET   $key"
  fi
}

echo "=== Seeding Azure Key Vault: $VAULT_NAME ==="
echo ""

# ── Authentik (central instance) ─────────────────────────────────────────────

echo "── bike-weather-auth (Authentik) ──"
AUTH_PG_PASS=$(generate_password)
AUTH_SECRET_KEY=$(generate_secret_key)

set_secret "bike-weather-auth-postgres-password" "$AUTH_PG_PASS"
set_secret "bike-weather-auth-secret-key"        "$AUTH_SECRET_KEY"
set_secret "bike-weather-auth-smtp-username"     "PLACEHOLDER-gmail-address"
set_secret "bike-weather-auth-smtp-password"     "PLACEHOLDER-gmail-app-password"

echo ""

# ── Production (bike-weather.com) ────────────────────────────────────────────

echo "── bike-weather (production) ──"
PROD_PG_PASS=$(generate_password)

set_secret "bike-weather-postgres-password"     "$PROD_PG_PASS"

# Authentik API token — must be created manually in Authentik after first boot,
# then updated here. Placeholder for now.
set_secret "bike-weather-authentik-api-token"    "PLACEHOLDER-create-in-authentik-admin"

echo ""

# ── Preview (preview.bike-weather.com) ───────────────────────────────────────

echo "── bike-weather-preview ──"
PREVIEW_PG_PASS=$(generate_password)

set_secret "bike-weather-preview-postgres-password"     "$PREVIEW_PG_PASS"

# Authentik API token for preview — same Authentik instance, separate app
set_secret "bike-weather-preview-authentik-api-token"    "PLACEHOLDER-create-in-authentik-admin"

# htpasswd basic-auth credentials for preview
set_secret "bike-weather-preview-htpasswd-username"      "preview"
set_secret "bike-weather-preview-htpasswd-password"      "$(generate_password)"

echo ""

# ── Summary ──────────────────────────────────────────────────────────────────

echo "=== Done ==="
echo ""
echo "Post-deployment steps:"
echo "  1. Access Authentik at https://auth.bike-weather.com/if/flow/initial-setup/"
echo "     and complete initial admin setup (set akadmin password)."
echo "  2. Run setup_authentik_homelab.py to create OAuth2 providers, applications,"
echo "     API tokens, and store them in Key Vault:"
echo "       python3 scripts/setup_authentik_homelab.py"
echo "  3. Add DNS records pointing to your internet gateway:"
echo "       bike-weather.com         → A record"
echo "       preview.bike-weather.com → A record (or CNAME)"
echo "       auth.bike-weather.com    → A record (or CNAME)"
