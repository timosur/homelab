# Design

## Context

The existing `agentgateway-mcp-api-keys` ExternalSecret syncs several backend keys from Azure Key Vault. Obscura v0.2.5 requires `OBSCURA_MCP_TOKEN` to be at least 32 bytes for its non-loopback MCP listener and validates the HTTP Authorization bearer token. Its environment is read at process startup.

## Goals / Non-Goals

**Goals:** Use the existing secret delivery and backend credential patterns; keep token values out of Git and tool output.

**Non-Goals:** Change inbound gateway authentication, other backends, or build an automated token rotation mechanism.

## Decisions

- Map Key Vault `agentgateway-obscura-mcp-token` to `obscura-mcp-token` in the existing Kubernetes Secret, consistent with other backend credentials. A separate ExternalSecret would isolate sync failures, but the requested shared secret follows existing conventions.
- Inject the Secret key as `OBSCURA_MCP_TOKEN` into Obscura and configure the Obscura target to send it in `Authorization` with the `Bearer ` prefix. This matches the deployed Obscura version and existing MCP credential syntax.
- Use the Obscura Service's cluster DNS name as the target host. The installed CRD rejects per-target policies with `backendRef`; a host-based target supports the required backend-specific authentication without applying the credential to other targets.

## Risks / Trade-offs

- [Missing Key Vault key] → The shared ExternalSecret sync can fail until the user creates the key; create it before deployment.
- [Token rotation] → Obscura reads environment variables at startup; coordinate a deployment restart with credential rotation to prevent mismatched credentials.
- [Backend health] → Ready pods alone do not prove end-to-end MCP initialization; check authentication and gateway behavior after deployment when the token is available.

## Migration Plan

Create the Key Vault secret with a token of at least 32 bytes before syncing the changes. Apply via the existing GitOps workflow, then verify secret sync, pod readiness, and MCP behavior. Roll back repository configuration if needed, noting the old configuration leaves Obscura v0.2.5 unable to start.
