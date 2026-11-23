# Spec Delta

## Purpose

Keep Grafana available through the home gateway and its internal health and provisioning clients.

## ADDED Requirements

### Requirement: Grafana is available through the home gateway
The system SHALL serve Grafana through the home gateway over HTTPS.

#### Scenario: Browser connects to Grafana
- **WHEN** a user connects to Grafana through its home gateway hostname
- **THEN** the gateway terminates HTTPS and forwards the request to Grafana successfully

### Requirement: Internal clients can reach Grafana
Grafana SHALL accept the protocol used by its health probes and provisioning sidecars.

#### Scenario: Health probes run
- **WHEN** Grafana is healthy and Kubernetes performs its health probes
- **THEN** the probes succeed without an HTTP-to-HTTPS handshake error

#### Scenario: Provisioning reload is requested
- **WHEN** a Grafana provisioning sidecar requests a reload
- **THEN** the request reaches Grafana without an HTTP-to-HTTPS handshake error
