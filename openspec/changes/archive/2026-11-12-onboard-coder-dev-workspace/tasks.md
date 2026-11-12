# Tasks

## 1. Companion bootstrap and workspace image

- [x] 1.1 Complete and review the companion `../dotfiles` change that supplies an executable, non-interactive Coder/Ubuntu Chezmoi path; verify it does not run Homebrew, Vaultwarden login, macOS scripts, or secret prompts.
- [x] 1.2 Add the Debian/Ubuntu Coder workspace image definition with the approved non-root development user, passwordless sudo, APT, OpenSSH, Git, Python/uv, Node.js/OpenSpec, kubectl/Kustomize, Helm, Ansible, and Chezmoi; verify the image builds and every required executable is available as that user.
- [ ] 1.3 Add image CI that validates pull requests without publication and publishes immutable amd64 digest tags after main-branch review; verify a test build does not embed credentials and the template can reference its resolved digest.

## 2. Coder control plane and private access

- [x] 2.1 Create the Coder namespace, Kustomize application resources, and ArgoCD Application; verify ArgoCD renders the application and its resources without creating public-route resources.
- [x] 2.2 Provision Coder's dedicated central-CNPG database through an AppDBClaim and wire its generated connection secret to Coder; verify Coder starts against the managed database and no separate PostgreSQL workload is rendered.
- [x] 2.3 Configure Coder's OIDC and GitHub OAuth secret references, Authentik client and exact callback, and isolated current-address email verification using the existing SMTP account; verify unverified addresses are rejected, the last confirmed address may be restored, confirmed addresses can complete OIDC login, and rendered manifests contain no credentials.
- [x] 2.4 Add the home-only HTTPRoute and DNS/TLS configuration for `coder.home.timosur.com`; verify LAN HTTPS access succeeds and Pangolin/Newt configuration contains no Coder route.
- [x] 2.5 Configure and document Coder's GitHub external-auth provider for HTTPS Git URLs; verify the personal workspace cannot start before required authorization and a private HTTPS clone succeeds without a Kubernetes-mounted personal credential.

## 3. Persistent Coder workspace template

- [x] 3.1 Add the version-pinned Coder Terraform template and provider/module dependencies; verify template validation and publication succeed from committed sources.
- [ ] 3.2 Implement a per-board-member workspace resource with the immutable workspace-image digest, affinity restricted to `homelab-amd-desktop` and `homelab-gpu`, both workers' taint tolerations, unprivileged service account, and a dynamically provisioned 100 GiB Synology RWO home PVC per workspace; verify a fresh workspace schedules only to an approved worker, does not share another user's PVC, and uses neither hostPath/local-path nor Dark Factory storage.
- [x] 3.3 Configure Coder's native dotfiles module with `default_dotfiles_uri` set to `https://github.com/timosur/dotfiles.git` and Coder's 30-minute inactivity stop policy; verify bootstrap succeeds without interactivity, workspace restart retains home data, and an inactive workspace stops while retaining its PVC.
- [ ] 3.4 Document the board-member workspace lifecycle, image-versus-APT persistence boundary, GitHub external-auth flow, `coder login`/`coder config-ssh`, and `zed ssh://coder.<workspace>` connection path; verify the documented Zed connection opens the running workspace without direct desktop-host SSH.

## 4. Desktop power coordination

- [x] 4.1 Add a narrowly authorized internal `wol-proxy` development-workspace preflight that wakes the fixed worker pool, waits for both workers' Ready and schedulable state, audits accepted actions, and fails without creating/deleting workspace storage when state is unknown or readiness times out; verify authorized wake, denied caller, timeout, and retry behavior with tests.
- [x] 4.2 Invoke the preflight as an ordered dependency before the Coder template creates workspace storage or workload; protect the handoff with a durable reservation and a separate fail-closed Coder scheduling gate, and verify starts from sleeping workers, interrupted starts, delayed pod creates, and suspension races.
- [ ] 4.3 Extend `wol-proxy` power-state observation to recognize labeled Coder workspaces and block pool suspension while they exist; verify the pool remains awake for an active workspace and another protected workload, then becomes eligible for existing suspension handling only after the workspace stops.
- [ ] 4.4 Run the documented end-to-end acceptance sequence: sleeping-node start, Authentik login, GitHub authorization, dotfiles bootstrap, Zed connection, persistent-data restart, 30-minute inactivity stop, and safe post-stop suspension; record results without exposing secret values.

## Workflow follow-up

- Archive the change after the companion dotfiles work, implementation, review, and acceptance checks are complete.
