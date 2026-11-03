# Obscura MCP Authentication

## Purpose

Provide authenticated connectivity between the gateway and the Obscura MCP backend without exposing token values in configuration.

## ADDED Requirements

### Requirement: Authenticated Obscura connectivity
The gateway SHALL authenticate to Obscura using the same externally managed token that Obscura uses to validate MCP requests.

#### Scenario: Valid token is available
- **WHEN** a valid token is provisioned and the configuration is deployed
- **THEN** Obscura can start and the gateway can authenticate MCP requests to it

#### Scenario: Unauthenticated request reaches Obscura
- **WHEN** an MCP request reaches Obscura without the configured token
- **THEN** Obscura rejects the request

### Requirement: Credential confidentiality
The token SHALL be supplied through the existing external secret management system and SHALL NOT be stored as plaintext in repository configuration.

#### Scenario: Manifests are rendered
- **WHEN** configuration is rendered or inspected
- **THEN** it contains only references to the token and not its value
