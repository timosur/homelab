#!/usr/bin/env bash
# Provision the Coder OIDC client without accepting or printing secret values.
#
# Required environment variables:
#   AUTHENTIK_API_TOKEN       API token with permission to manage OAuth2 providers
#   CODER_OIDC_CLIENT_SECRET  Confidential client secret to store in Azure Key Vault
set -euo pipefail

: "${AUTHENTIK_API_TOKEN:?set AUTHENTIK_API_TOKEN outside this repository}"
: "${CODER_OIDC_CLIENT_SECRET:?set CODER_OIDC_CLIENT_SECRET outside this repository}"

exec python3 "$(dirname "$0")/setup_authentik_homelab.py" \
  --provision-oidc \
  --base-url https://auth.timosur.com \
  --namespace authentik \
  --server-selector app=authentik-server \
  --app-name Coder \
  --slug coder \
  --client-id coder \
  --redirect-uri https://coder.home.timosur.com/api/v2/users/oidc/callback \
  --launch-url https://coder.home.timosur.com/ \
  --allowed-group board-member \
  --secret-namespace coder \
  --secret-name unused-with-key-vault \
  --api-token-env AUTHENTIK_API_TOKEN \
  --client-secret-env CODER_OIDC_CLIENT_SECRET \
  --keyvault-client-id-key coder-oidc-client-id \
  --keyvault-client-secret-key coder-oidc-client-secret
