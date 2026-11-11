#!/bin/sh
# The provisioner reads its service credential from a mounted secret, not Terraform state.
set -eu
set +x

: "${WORKSPACE_ID:?workspace identity is required}"
IFS= read -r token </run/coder-power/token || [ -n "${token:-}" ]
case "$token" in
  ''|*[!A-Za-z0-9_-]*) printf '%s\n' 'Invalid power preflight credential' >&2; exit 1 ;;
esac

# Pass the header over a pipe rather than putting the credential in process arguments.
printf 'header = "Authorization: Bearer %s"\n' "$token" | curl \
  --config - --silent --show-error --fail --max-time 150 \
  --cacert /run/coder-power-ca/ca.crt \
  --header 'Content-Type: application/json' \
  --data "{\"workspace_id\":\"$WORKSPACE_ID\"}" --output /dev/null \
  https://dark-factory-admission.wol-proxy.svc/coder/preflight
