# Design

## Context

See [proposal.md](proposal.md) for motivation and
[coder-dev-workspaces specification](specs/coder-dev-workspaces/spec.md) for behavior. The cluster
already provides ArgoCD, the home Envoy Gateway, Authentik, External Secrets backed by Azure Key
Vault, Crossplane-managed CloudNativePG databases, Synology CSI storage, and an always-on
`wol-proxy` on the control plane. `homelab-amd-desktop` is a demand-woken worker tainted
`availability=daytime:NoSchedule`.

Dark Factory's Kata runtime, task NFS storage, admission service, and workspace ownership records
are designed for untrusted autonomous tasks. They are not an appropriate dependency for an
interactive personal development environment.

## Goals / Non-Goals

**Goals:**

- Add one private Coder control plane and repeatable, persistent development workspaces for board
  members.
- Reuse existing identity, secret, database, storage, ingress, GitOps, and power-management
  systems without exposing credentials or services publicly.
- Make Zed access use Coder's supported SSH configuration and make GitHub authentication Coder
  managed rather than a persistent workspace secret.
- Ensure a stopped workspace can release the desktop worker for normal power-safety handling.

**Non-Goals:**

- General multi-user Coder tenancy, per-repository workspace catalogs, external access, a direct
  SSH proxy, host development, Docker-in-Docker, GPU development, or Kubernetes credentials in the
  workspace.
- Reuse or change Dark Factory's Kata, task-workspace, admission, or retention behavior.
- Merge the companion dotfiles-repository implementation into this repository's change.

## Decisions

### Coder is the workspace control plane

Deploy Coder as a dedicated ArgoCD application and namespace, using its supported Kubernetes
workspace template and Terraform provider resources. Expose its control plane only with a home
HTTPRoute at `coder.home.timosur.com`; configure `CODER_ACCESS_URL` and Authentik OIDC to that
canonical HTTPS address. Provision an AppDBClaim for Coder and wire the resulting database secret
into Coder. Store OIDC and GitHub OAuth client secrets in Azure Key Vault and materialize them with
External Secrets.

Authentik's default email scope reports addresses as unverified. Keep Coder's verification check
enabled and use a Coder-specific scope mapping that returns `email_verified` only when the current
email matches an address confirmed through an isolated Authentik email-verification flow. That
flow must require a successful email confirmation before recording the verified address, and must
not alter unrelated applications' login flows. Reuse the existing iCloud SMTP account and its
Key Vault `smtp-password` reference for homelab Authentik; never store the credential in Git.
A different email is unverified until confirmed. If the user changes back to the last confirmed
address before confirming its replacement, the earlier confirmation remains valid. This accepts the
risk that the user may no longer control that previously confirmed mailbox.

Coder owns workspace lifecycle, access proxying, SSH configuration, and activity tracking. A user
authenticates with `coder login`, runs `coder config-ssh`, and opens `zed ssh://coder.<workspace>`.

**Alternatives considered:** A bespoke SSH proxy plus a long-lived pod would duplicate Coder's
workspace, access, and inactivity behavior. Direct host SSH is not a dev container boundary.
Using a host-managed VM would introduce an unimplemented libvirt/QEMU management plane. A Kata
workspace adds overhead and couples this personal environment to the Dark Factory sandbox domain.

### Per-board-member pool-scheduled persistent pod template

The initial template lets each board member create an independent standard Kubernetes development
pod with required node affinity for `homelab-amd-desktop` or `homelab-gpu` and tolerations for
`availability=daytime:NoSchedule` and `gpu=true:NoSchedule`. It dynamically provisions one 100 GiB Synology CSI
ReadWriteOnce PVC per workspace and mounts it at that workspace user's home. The template's
workload labels identify it as a Coder development workspace but do not grant it a Kubernetes
service account beyond the default no-privilege identity.

The workspace image is built and published by this repository, pinned by immutable digest in the
template, and contains a Debian/Ubuntu base, SSH support, Git, Python/uv, Node.js/OpenSpec,
kubectl/Kustomize, Helm, Ansible, APT, and a non-root personal user with passwordless sudo. Image
rebuilds are the durable route for common tools; APT changes are explicitly disposable across
workspace rebuilds.

**Alternatives considered:** Local-path or hostPath storage would tie the workspace data to one
node and is not suitable for this workload. Dark Factory NFS storage has different ownership and
failure semantics. A long-lived host VM would keep package changes but requires a new host
virtualization control plane.

### Coder-managed GitHub and dotfiles bootstrap

Configure Coder's GitHub external-auth provider and require it from the workspace template. Git
uses HTTPS and Coder's injected `GIT_ASKPASS` path; no personal PAT or SSH key is placed in the pod
specification, PVC, or image.

Use Coder's native dotfiles module with the personal dotfiles repository. Before enabling it, a
separate dotfiles-repository change must provide an executable, non-interactive Coder/Ubuntu-safe
setup path. That path uses Chezmoi to apply user configuration and explicitly avoids Homebrew,
macOS-only scripts, Vaultwarden login, and prompting for secret values. The homelab template pins
the repository's default branch. This deliberately accepts that a later dotfiles change can break
Coder bootstrap; keep the companion Coder/Ubuntu-safe path covered by its own tests before merging
dotfiles changes.

**Alternatives considered:** Mounting a Key Vault GitHub token enlarges the compromise boundary and
leaves durable credentials in the workspace. A custom clone-and-apply script bypasses Coder's
native dotfiles lifecycle and would require parallel update behavior.

### Power coordination is a bounded preflight and observation integration

Add an authenticated internal `wol-proxy` development-workspace power interface. The Coder
template invokes its start preflight before creating workspace storage or the Kubernetes workload;
the proxy wakes the shared worker pool and reports success only after both workers are Ready and
schedulable and the existing pool fence permits starts. The caller
uses a narrowly scoped service credential, and the proxy validates only the named personal Coder
workspace action.

Serialize the preflight-to-pod handoff with suspension using a durable start reservation. An
interrupted or uncertain handoff remains fail-closed across proxy restart. A separate fail-closed
Coder pod admission gate prevents delayed pod creates from bypassing the shared pool interlock;
only the wake controller may release that gate while holding the fenced release phase. Observation
of existing, pending, or deleting Coder workspace pods also blocks pool suspension. Do not assume a
successful preflight or an observed pod alone makes a later pod creation safe.

Configure Coder's template activity policy to stop the workspace after 30 minutes with no active
workspace activity. On stop, Coder removes the workload while preserving the PVC. `wol-proxy`
observes the labeled workspace workload and includes its state in the existing desktop group power
decision; it must not suspend the host while the workspace exists or another protected workload
requires it. Power control remains fail-safe: unknown node, workload, or proxy state prevents a
suspend request and surfaces a recoverable error rather than deleting workspace storage.

**Alternatives considered:** An SSH wake-and-relay endpoint would be redundant with Coder's SSH
proxy and cannot safely infer workspace lifecycle. Letting a pending pod wake the host lacks a
reliable trigger while its node is asleep. Independent suspension logic would bypass the current
power guard.

## Risks / Trade-offs

- [The desktop is asleep or fails to rejoin before a build deadline] → Wake before provisioning,
  wait for both workers' Ready/schedulable state, report the failed build without creating or deleting the PVC,
  and allow an explicit retry.
- [A power API is called outside the approved Coder workflow] → Authenticate and authorize a
  single named workspace action for the fixed pool; deny all other power requests and audit accepted requests.
- [Coder inactivity detection does not see an expected client session] → Treat workspace stop as
  intentional/restartable, preserve the PVC, and document the 30-minute behavior before rollout.
- [The Coder control plane or OAuth integration is compromised] → Keep it LAN-only, require
  Authentik login, store client secrets outside Git, scope GitHub authorization, and avoid mounting
  user credentials in workspace resources.
- [The dotfiles installer runs the existing interactive setup] → Make the companion dotfiles change
  a deployment prerequisite and test its Coder/Ubuntu path before enabling the native module.
- [A later dotfiles change breaks Coder bootstrap] → Keep the companion Coder/Ubuntu-safe path
  covered by its own tests before merging dotfiles changes; no revision pin is enforced.
- [APT-installed tools disappear after rebuild] → State this behavior in workspace documentation
  and move durable tools into the image build.

## Migration Plan

1. Prepare and review the companion `dotfiles` change, including a non-interactive Coder/Ubuntu
   bootstrap test; do not enable Coder dotfiles until that revision is available.
2. Build and publish the immutable workspace-image digest, then provision Coder's database,
   secrets, Authentik client, GitHub OAuth provider, and LAN-only route through GitOps.
3. Deploy the power preflight/observation integration with power actions disabled by default; test
   authorization failure, sleeping-node wake, Ready timeout, active-workload protection, and
   recovery without a workspace PVC deletion.
4. Publish the Coder template, create the single personal workspace, authorize GitHub external
   authentication, configure local Coder SSH, and validate a Zed connection, persistent home,
   dotfiles installation, and 30-minute inactivity stop.
5. Enable automatic desktop suspension only after the end-to-end wake/stop tests demonstrate that
   existing protected workloads continue to block suspension.

To roll back, disable new workspace starts and automatic suspension first, stop the Coder workspace,
and preserve its PVC. Remove the Coder route and application only after backing up or deliberately
retiring the Coder database; do not delete the workspace PVC or database as part of an unreviewed
rollback.
