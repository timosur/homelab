# Design

## Context

The home gateway's HTTPS listener terminates TLS. Grafana's chart-generated health probes, provisioning sidecars, and gateway backend use HTTP, while its listener currently expects HTTPS.

## Goals / Non-Goals

**Goals:** Restore compatibility with existing internal clients without weakening browser-facing HTTPS.

**Non-Goals:** Change gateway routing, Grafana authentication, or Mealie storage.

## Decisions

Set Grafana's internal server protocol to HTTP while retaining the HTTPS `root_url` and secure cookies. This matches the established TLS-terminating gateway and existing chart-generated clients. Keeping backend HTTPS instead would require configuring the gateway, probes, and sidecar reload clients consistently, including certificate trust.

## Risks / Trade-offs

- Internal gateway-to-Grafana traffic is unencrypted → retain HTTPS at the gateway and the existing network boundary; do not claim end-to-end TLS.
- Rolling deployment could expose unrelated readiness issues → verify health, reloads, and new logs after reconciliation. Reverting the setting restores the prior behavior, including the mismatch.
