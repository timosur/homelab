# Coder development workspace handoff

## Scope and branch

This branch stages the `onboard-coder-dev-workspace` OpenSpec change: a private Coder control
plane at `coder.home.timosur.com`, independent persistent workspaces for board members, and later
desktop wake/suspension coordination. It is not deployed or ready to archive.

## Completed local work

- The workspace image was committed and published separately as
  `ghcr.io/timosur/homelab/coder-workspace:sha-fd710c66858d82b465d6c2973345dd248e4677c9`.
  The template pins its manifest digest:
  `sha256:db6aa41f4886ebef94e6a7915c6299e83b4d6eb154c647c625ac524e3be3a102`.
- The companion `../dotfiles` Coder/Ubuntu-safe bootstrap change is at commit
  `1d70b053cb8e2840623faa71c680ee15bf45c5a0`. An annotated tag with the corresponding
  `coder-safe-` name exists, but Coder uses the dotfiles repository's default branch because the
  stock native module version does not accept a branch or tag input.
- `apps/coder/` contains the namespace, Coder Deployment and Service, central-CNPG `AppDBClaim`,
  ExternalSecret references, controller RBAC for workspace Pods/PVCs, and Kustomization.
- `apps/coder/templates/board-member/` contains a version-pinned Terraform template. It creates a
  separate 100 GiB `homelab-iscsi` RWO PVC per workspace, starts the Pod only while a workspace is
  running, requires Coder GitHub external authentication, pins the workspace image digest, targets
  either `homelab-amd-desktop` or `homelab-gpu`, tolerates `availability=daytime:NoSchedule`, and disables the workspace
  Pod service-account token.
- `publish.sh` publishes the template and configures a 30-minute default autostop and activity bump.
- `.terraform/` is ignored; the generated template `.terraform.lock.hcl` is intentionally tracked.
- The ArgoCD application and home-only HTTPRoute are wired into their parent Kustomizations. There
  is no Pangolin/Newt route.

## Authentik and Key Vault state

The reusable `scripts/setup_authentik_homelab.py --provision-oidc` path was run against the
`homelab` Kubernetes context. It created the confidential Authentik **Coder** provider and the
`coder` application with:

- callback `https://coder.home.timosur.com/api/v2/users/oidc/callback`;
- launch URL `https://coder.home.timosur.com/`;
- `board-member` group mapping.

Its attempt to write Key Vault values failed because the active Azure CLI tenant was not accepted
by Key Vault `homelab-timosur`. No credential values are recorded in this repository or handoff.
The initially generated Authentik client secret was not stored and must be replaced.

Before deploying, log Azure CLI into a tenant accepted by that Key Vault, create a replacement
confidential OIDC secret locally, then run `scripts/setup_coder_authentik.sh` with local
`AUTHENTIK_API_TOKEN` and `CODER_OIDC_CLIENT_SECRET` values. That wrapper updates the existing
provider and writes:

- `coder-oidc-client-id`
- `coder-oidc-client-secret`

Create the GitHub OAuth client separately and add:

- `coder-github-oauth-client-id`
- `coder-github-oauth-client-secret`

Its callback must be `https://coder.home.timosur.com/external-auth/github/callback`, matching
`CODER_EXTERNAL_AUTH_0_ID=github` in `apps/coder/configmap.yaml`.

## Validation completed

- `terraform init -backend=false -upgrade=false`, `terraform validate`, and `terraform fmt -check`
  in `apps/coder/templates/board-member/`.
- `kubectl kustomize apps/coder`.
- `openspec validate onboard-coder-dev-workspace --strict`.
- `git diff --check`.

The repository-pinned shellcheck wrapper cannot run on this machine because its `rtk` executable is
missing. `bash -n apps/coder/templates/board-member/publish.sh` passed instead.

## Remaining work

1. Complete Key Vault and GitHub OAuth configuration above, then deploy the ArgoCD application.
2. Verify the AppDBClaim, ExternalSecret, Authentik login, and LAN-only route live.
3. Review and enable the staged power coordination. The authenticated pooled preflight,
   durable interlock reservation, separate Coder scheduling gate, and workspace observation are
   implemented locally but intentionally not enabled or deployed. Follow
   `apps/coder/power-coordination/README.md` before publishing the template.
4. Run `apps/coder/templates/board-member/publish.sh` while logged in to Coder; create a
   board-member workspace and verify its PVC, GitHub external auth, dotfiles bootstrap, Zed SSH,
   and live power coordination (tasks 4.1–4.3).
5. Add the user-facing lifecycle documentation and perform the end-to-end acceptance sequence.
6. Check and update `openspec/changes/onboard-coder-dev-workspace/tasks.md` only as the live checks
   complete; sync and archive the change only after all tasks pass.
