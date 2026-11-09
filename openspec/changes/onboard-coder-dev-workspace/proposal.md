# Proposal

## Why

Developing against the homelab currently requires a separately managed local environment or direct
access to cluster nodes. A personal, persistent remote workspace accessible from Zed should provide
a repeatable development environment without exposing the desktop worker or Coder to the internet.

## What Changes

- Onboard self-hosted Coder as the LAN-only personal development-workspace platform at
  `coder.home.timosur.com`, using Authentik OIDC and a dedicated central-CNPG database provisioned
  through an AppDBClaim.
- Provide a Coder workspace template for each board member on the demand-woken
  `homelab-amd-desktop` worker. Each workspace uses its own 100 GiB Synology RWO volume and a
  purpose-built Debian/Ubuntu image with the homelab infrastructure toolchain and interactive APT
  access through passwordless `sudo`.
- Integrate Coder workspace start and stop with `wol-proxy`: wake and await the desktop worker
  before provisioning, allow Coder's Zed-compatible SSH access, stop an inactive workspace after
  30 minutes, and request host suspension only after the workspace has stopped and existing power
  safeguards allow it.
- Configure Coder's built-in GitHub external authentication for HTTPS Git access so no personal Git
  credential is mounted into the workspace.
- Add a required companion change in the `dotfiles` repository that makes its Coder/Ubuntu setup
  non-interactive and safe; use Coder's native dotfiles module with the repository's default branch
  rather than the repository's current macOS-oriented interactive setup script.
- Do not expose Coder or workspace SSH through Pangolin/Newt, develop directly on the desktop host,
  or reuse Dark Factory's task workspace and Kata sandbox domain.

## Capabilities

### New Capabilities
- `coder-dev-workspaces`: Private Coder access, persistent personal Kubernetes workspaces, Zed
  connectivity, controlled desktop power coordination, and workspace bootstrap behavior.

### Modified Capabilities
- None.

## Impact

The change adds a Coder GitOps application, ArgoCD application, home HTTPS route, Authentik OIDC
client configuration, Crossplane AppDBClaim, Coder workspace Terraform template, workspace-image
build and publication workflow, and narrowly scoped `wol-proxy` power-coordination API and policy.
It also requires a separately reviewed change to `../dotfiles`. Coder, its workspace template, the
workspace image, Coder's Terraform/provider modules, and GitHub OAuth become new operational
dependencies.
