# Tasks

## 1. Configure authentication

- [x] 1.1 Map Key Vault `agentgateway-obscura-mcp-token` to `obscura-mcp-token` in the existing ExternalSecret; verify the rendered mapping and preservation of existing keys.
- [x] 1.2 Inject the Secret key as `OBSCURA_MCP_TOKEN` into Obscura; verify the rendered deployment references the Secret without embedding a value.
- [x] 1.3 Configure the Obscura gateway target to use the same key as a bearer credential; verify the rendered backend and Kubernetes schema.

## 2. Integration verification

- [x] 2.1 Render the complete Kustomize application and validate the change with OpenSpec.
- [x] 2.2 After the user provisions the Key Vault secret and GitOps deploys the change, verify secret sync, Obscura readiness, and MCP authentication without exposing token values; report any blocked checks.
