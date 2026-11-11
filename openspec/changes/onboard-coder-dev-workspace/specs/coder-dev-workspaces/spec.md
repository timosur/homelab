# Spec Delta

## Purpose

Provide a private, persistent Coder development workspace for the homelab that Zed can access
securely while coordinating the demand-woken desktop worker's power state.

## ADDED Requirements

### Requirement: Private authenticated Coder access
The system SHALL provide the Coder control plane only at `coder.home.timosur.com` through the home
HTTPS gateway. It SHALL authenticate users with the configured Authentik OIDC provider and SHALL
not publish a Coder endpoint through Pangolin, Newt, or another internet-facing route.

#### Scenario: Authorized LAN user opens Coder
- **WHEN** an authorized user visits `coder.home.timosur.com` from the home network
- **THEN** the user is redirected to Authentik when unauthenticated and can reach Coder after a
  successful OIDC login

#### Scenario: Public route reconciliation
- **WHEN** internet-facing route configuration is rendered
- **THEN** it contains no route that exposes the Coder control plane

### Requirement: Managed Coder persistence and secrets
The system SHALL provision Coder with a dedicated database and connection credentials through the
central managed PostgreSQL service. Coder configuration secrets, including OIDC and GitHub OAuth
client credentials, SHALL be sourced through the existing external-secret system and never stored
as plaintext in committed manifests.

#### Scenario: Coder database is provisioned
- **WHEN** the Coder application is reconciled
- **THEN** it receives a dedicated managed database connection without deploying an independent
  PostgreSQL server

#### Scenario: Manifests are inspected
- **WHEN** Coder configuration is rendered or reviewed
- **THEN** only secret references are present and credential values are absent

### Requirement: Persistent desktop development workspace
The system SHALL provide each board member a Coder workspace template that starts a standard
Kubernetes development pod on either `homelab-amd-desktop` or `homelab-gpu`, tolerates the applicable scheduling constraints,
and mounts a dedicated 100 GiB Synology ReadWriteOnce volume as the development home directory.
Stopping and restarting the workspace SHALL preserve that directory without sharing it with another
board member's workspace.

#### Scenario: Workspace starts after a normal stop
- **WHEN** a board member's workspace is stopped and then started again
- **THEN** it runs on one of the two approved workers and contains the prior development home-directory data

#### Scenario: Workspace storage is provisioned
- **WHEN** a board member's workspace is first created
- **THEN** its home directory is backed by a dedicated 100 GiB Synology ReadWriteOnce volume that
  is distinct from other board members' volumes, rather than node-local, hostPath, or Dark Factory
  task-workspace storage

### Requirement: Zed-compatible workspace access
The system SHALL make the running personal workspace reachable through Coder's supported SSH
configuration so a user can open it with Zed. The workspace SHALL not require a separate
internet-facing SSH listener or direct SSH access to the desktop worker host.

#### Scenario: Zed opens a running workspace
- **WHEN** the user has configured Coder SSH access and selects the personal running workspace in
  Zed
- **THEN** Zed connects to the workspace through Coder without connecting directly to the desktop
  worker host

### Requirement: Workspace development environment and bootstrap
The workspace SHALL run a Debian/Ubuntu-based image containing OpenSSH, Git, Python with uv,
Node.js with OpenSpec, kubectl, Kustomize, Helm, Ansible, APT, and passwordless sudo for the
personal development user. The template SHALL require Coder GitHub external authentication for
HTTPS Git operations and SHALL install the configured dotfiles through Coder's native dotfiles
integration without mounting a personal Git credential in the workspace.

#### Scenario: Private Git repository access
- **WHEN** the user authorizes the required GitHub external-auth provider and runs an HTTPS Git
  operation against an approved private repository
- **THEN** the operation authenticates through Coder without a GitHub token or SSH private key
  stored in the workspace manifest or persistent home volume

#### Scenario: Dotfiles bootstrap
- **WHEN** the workspace is first provisioned or its dotfiles installation is requested
- **THEN** Coder installs the configured dotfiles through a non-interactive Coder/Ubuntu-safe path
  that does not invoke macOS setup or interactive secret-login behavior


### Requirement: Demand-woken workspace lifecycle
The system SHALL use `wol-proxy` to wake the shared worker pool before workspace provisioning.
The start handoff SHALL prevent suspension until the workload is safely observed. Coder SHALL stop
the workspace after 30 minutes without activity. `wol-proxy` SHALL suspend the pool only when the
workspace has stopped and existing power-safety conditions permit it.

#### Scenario: Start while a pool worker is asleep
- **WHEN** the user starts the workspace while either worker is asleep
- **THEN** the pool is woken and both workers are observed Ready and schedulable before the workspace is allowed to run

#### Scenario: Start races suspension
- **WHEN** workspace start is in progress or its outcome is uncertain
- **THEN** suspension remains blocked until the handoff to an observed workload is safe or an operator resolves the uncertainty

#### Scenario: Workspace becomes inactive
- **WHEN** no Coder workspace activity is detected for 30 minutes
- **THEN** Coder stops the workspace and the persistent development home remains available for the
  next start

#### Scenario: Other protected workload remains active
- **WHEN** the Coder workspace has stopped but a protected workload still requires the desktop
- **THEN** `wol-proxy` does not suspend the desktop worker
