# Proposal

## Why

Obscura v0.2.5 refuses to start its non-loopback MCP server without a token, leaving its gateway backend unavailable. The gateway must supply the same token for authenticated requests.

## What Changes

- Sync the Obscura MCP token from Azure Key Vault into the existing MCP credentials Secret.
- Provide the token to Obscura and configure the gateway to send it as a bearer credential to that backend.
- Preserve existing MCP backend credentials and verify the rendered configuration and, when available, live recovery.

## Capabilities

### New Capabilities

- `obscura-mcp-authentication`: Secure, authenticated connectivity between agentgateway and the Obscura MCP backend.

### Modified Capabilities

None.

## Impact

Agentgateway ExternalSecret, Obscura deployment, MCP backend configuration, and the Azure Key Vault secret `agentgateway-obscura-mcp-token`. The user provides the secret value before deployment.
