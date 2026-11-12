# Spec Delta

## Purpose

Reduce Coder development workspace access to internal resources and add runtime defenses against
common container escape paths without removing the normal personal development workflow.

## ADDED Requirements

### Requirement: Workspace internal egress isolation
The system SHALL restrict Coder workspace egress from startup to cluster DNS and the minimum
verified Coder agent connectivity, while permitting public internet access. It SHALL deny direct
access to other LAN destinations, cluster workloads, node services, and the Kubernetes API for
all enabled address families. Other workloads SHALL NOT acquire these workspace restrictions.

#### Scenario: Internal destinations are blocked
- **WHEN** a workspace connects to representative LAN addresses, node addresses, the Kubernetes
  API, and unrelated cluster services using direct addresses or service names
- **THEN** the connections fail, including for any enabled IPv6 routes

#### Scenario: Essential connectivity remains available
- **WHEN** a workspace resolves public names, connects to public development services, and
  establishes its Coder agent connection
- **THEN** those operations succeed without granting general internal access

#### Scenario: Policy isolation
- **WHEN** a non-workspace pod in the Coder namespace or an unrelated namespace connects using
  its previously permitted policy
- **THEN** this workspace restriction does not select or disrupt that pod

### Requirement: Coder connectivity does not expose shared internal routes
The workspace's necessary Coder connectivity SHALL NOT give it a general-purpose route to other
home gateway applications or unrelated internal services. Permitted internal services are trusted
dependencies, not a guarantee against their compromise or all possible relays via the public internet.
The system SHALL permit the changing home public IP as public internet traffic, even when it
exposes a router management interface, as explicitly accepted by the user.

#### Scenario: Shared gateway route attempt
- **WHEN** a workspace attempts to use the permitted Coder connection path to access a different
  home gateway application by hostname, SNI, or HTTP host
- **THEN** the request cannot reach that application

#### Scenario: Home router is exposed at the public address
- **WHEN** a workspace connects to the home network's public IP through the public internet allowance
- **THEN** the egress policy does not block the connection solely because it reaches that public IP

### Requirement: Workspace pod isolation baseline
Workspace pods SHALL retain passwordless sudo and interactive APT while using a hardened
shared-kernel pod configuration that does not grant host namespaces, host mounts, privileged mode,
or a Kubernetes service-account token, and restricts kernel privileges and syscalls to those needed
by the approved development workflow.

#### Scenario: Hardened workspace starts
- **WHEN** a workspace pod is created from the approved template
- **THEN** the pod has the required isolation settings and the user can run sudo and install an
  APT package

### Requirement: Scoped runtime escape defenses
The system SHALL apply workspace-scoped runtime enforcement and event reporting for selected
escape-related operations supported by both eligible workers, including attempts to join another
namespace. It SHALL NOT represent these controls as a guarantee against all kernel exploits or
container escapes.

#### Scenario: Representative escape attempt
- **WHEN** a process in a workspace attempts a covered namespace-join operation
- **THEN** the configured runtime control prevents the attempt and records an attributable event

#### Scenario: Other workloads are unaffected
- **WHEN** a non-workspace pod performs the same operation
- **THEN** the workspace-specific runtime policy does not enforce on that pod

### Requirement: Verified rollout and operational visibility
Operators SHALL verify network enforcement, runtime-policy coverage, and normal workspace function
on both eligible workers before relying on the protection. A failed or inconclusive check SHALL block
the protected rollout, and operators SHALL have documented checks and a recovery path.

#### Scenario: Worker lacks policy coverage
- **WHEN** the runtime agent is absent, a policy is not loaded, or the effective network policy
  cannot be verified on an eligible worker
- **THEN** operators do not declare the protected rollout complete and preserve workspace data

#### Scenario: End-to-end compatibility
- **WHEN** protection checks pass on both workers
- **THEN** Coder and Zed access, GitHub authentication and HTTPS Git, DNS, sudo, APT, persistence,
  and existing start/stop power coordination remain usable
