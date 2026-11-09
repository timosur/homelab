# Tasks

## 1. Companion bootstrap and workspace image

- [x] 1.1 Complete and review the companion `../dotfiles` change that supplies an executable, non-interactive Coder/Ubuntu Chezmoi path; verify it does not run Homebrew, Vaultwarden login, macOS scripts, or secret prompts.
- [x] 1.2 Add the Debian/Ubuntu Coder workspace image definition with the approved non-root development user, passwordless sudo, APT, OpenSSH, Git, Python/uv, Node.js/OpenSpec, kubectl/Kustomize, Helm, Ansible, and Chezmoi; verify the image builds and every required executable is available as that user.
- [ ] 1.3 Add image CI that validates pull requests without publication and publishes immutable amd64 digest tags after main-branch review; verify a test build does not embed credentials and the template can reference its resolved digest.

## 2. Coder control plane and private access

- [x] 2.1 Create the Coder namespace, Kustomize application resources, and ArgoCD Application; verify ArgoCD renders the application and its resources without creating public-route resources.
- [ ] 2.2 Provision Coder's dedicated central-CNPG database through an AppDBClaim and wire its generated connection secret to Coder; verify Coder starts against the managed database and no separate PostgreSQL workload is rendered.
- [ ] 2.3 Add ExternalSecrets for Coder's OIDC and GitHub OAuth client configuration, and configure the Authentik OIDC client with the exact Coder callback URL; verify rendered manifests contain only secret references and an authenticated user can complete OIDC login.
- [ ] 2.4 Add the home-only HTTPRoute and DNS/TLS configuration for `coder.home.timosur.com`; verify LAN HTTPS access succeeds and Pangolin/Newt configuration contains no Coder route.
- [ ] 2.5 Configure and document Coder's GitHub external-auth provider for HTTPS Git URLs; verify the personal workspace cannot start before required authorization and a private HTTPS clone succeeds without a Kubernetes-mounted personal credential.

## 3. Persistent Coder workspace template

- [ ] 3.1 Add the version-pinned Coder Terraform template and provider/module dependencies; verify template validation and publication succeed from committed sources.
- [ ] 3.2 Implement a per-board-member workspace resource with the immutable workspace-image digest, `homelab-amd-desktop` selector, daytime-taint tolerance, unprivileged service account, and a dynamically provisioned 100 GiB Synology RWO home PVC per workspace; verify a fresh workspace schedules only to the desktop worker, does not share another user's PVC, and uses neither hostPath/local-path nor Dark Factory storage.
- [ ] 3.3 Configure Coder's native dotfiles module with `default_dotfiles_uri` set to `https://github.com/timosur/dotfiles.git` and Coder's 30-minute inactivity stop policy; verify bootstrap succeeds without interactivity, workspace restart retains home data, and an inactive workspace stops while retaining its PVC.
- [ ] 3.4 Document the board-member workspace lifecycle, image-versus-APT persistence boundary, GitHub external-auth flow, `coder login`/`coder config-ssh`, and `zed ssh://coder.<workspace>` connection path; verify the documented Zed connection opens the running workspace without direct desktop-host SSH.

## 4. Desktop power coordination

- [ ] 4.1 Add a narrowly authorized internal `wol-proxy` development-workspace preflight that wakes only `homelab-amd-desktop`, waits for Ready and schedulable state, audits accepted actions, and fails without creating/deleting workspace storage when state is unknown or readiness times out; verify authorized wake, denied caller, timeout, and retry behavior with tests.
- [ ] 4.2 Invoke the preflight as an ordered dependency before the Coder template creates the workspace workload; verify starting from a sleeping desktop wakes the node before the workspace pod runs.
- [ ] 4.3 Extend `wol-proxy` power-state observation to recognize the labeled Coder workspace and block suspension while it exists; verify the desktop remains awake for an active workspace and another protected workload, then becomes eligible for existing suspension handling only after the workspace stops.
- [ ] 4.4 Run the documented end-to-end acceptance sequence: sleeping-node start, Authentik login, GitHub authorization, dotfiles bootstrap, Zed connection, persistent-data restart, 30-minute inactivity stop, and safe post-stop suspension; record results without exposing secret values.

## Workflow follow-up

- Archive the change after the companion dotfiles work, implementation, review, and acceptance checks are complete.
