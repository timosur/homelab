# Proposal

## Why

Grafana listens for HTTPS while its health probes and provisioning sidecars send HTTP, causing TLS handshake errors and leaving its pods unready. The home gateway already terminates HTTPS.

## What Changes

- Align Grafana's internal listener with its HTTP clients while retaining HTTPS for the public gateway and URL.
- Verify Grafana readiness, provisioning reloads, and the absence of new protocol-mismatch errors after rollout.

## Capabilities

### New Capabilities
- `grafana-availability`: Grafana availability behind the home HTTPS gateway and compatibility with its in-cluster clients.

### Modified Capabilities
- None.

## Impact

- Grafana configuration in `apps/_argocd/monitoring-app.yaml`.
- Grafana health probes, provisioning sidecars, and home gateway traffic.
