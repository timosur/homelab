# Tasks

## 1. Workspace isolation

- [x] 1.1 Establish a supported Coder-only agent connection and workspace-scoped Cilium egress policy; verify rendered selectors, effective policy combinations, public DNS/internet and Coder connectivity, denial of LAN/node/API/unrelated cluster targets across enabled address families, and denial of other home routes via SNI/Host changes without affecting non-workspace pods.
- [x] 1.2 Harden the workspace pod security context while retaining sudo and APT; verify the rendered template excludes host access, privileged mode, a service-account token, and unnecessary capabilities, and test that sudo and an APT installation work with the selected seccomp profile.
- [x] 1.3 Add workspace-scoped Tetragon enforcement and event reporting for tested escape-related operations, including namespace joins; verify supported policy loading, an attributable blocked attempt, the seccomp interaction, and a non-workspace negative control.
- [x] 1.4 Document the allowed network paths, shared-kernel and trusted-service limitations, policy/agent health checks, and safe GitOps rollback; verify the documented recovery preserves workspace PVCs and does not bypass the power interlocks.

## 2. Integrated acceptance

- [x] 2.1 On both eligible workers, run controlled network and runtime probes and the Coder/Zed, GitHub HTTPS, DNS, sudo/APT, persistent-home and start/stop power workflows; record non-sensitive results and leave the protected rollout incomplete if any policy, worker coverage, or compatibility check fails.

## Verification so far

- On both workers, disposable pods using the installed profile ran sudo and installed an APT
  package. The profile matched each worker's realized containerd default except for `setns`.
- A disposable copy of the scoped Tetragon policy on each worker recorded and terminated a valid
  namespace-join attempt, without breaking `kubectl exec`; an unselected control was not killed.
- On the GPU worker, a disposable Cilium policy allowed DNS and public HTTPS while denying the LAN,
  API, unrelated pod and DNS metrics, including with a competing broad allow. A temporary isolated
  Gateway served Coder over verified TLS; another Host returned 404 and another SNI was rejected.
- Two Coder verification workspaces were created on the eligible workers using inactive template
  versions. Both connected over Coder SSH, reached GitHub and Coder, used private Git authentication,
  and denied direct LAN/API/other cluster paths. Their Tetragon namespace-join events were
  attributable, and a desktop workspace retained a marker across stop/start.
- User-confirmed Zed connections succeeded on each worker. The existing workspace was updated to
  `sandbox-final-20261112`, verified healthy, and returned to stopped state with its PVC retained.
  Forced-relay Coder ping and SSH succeeded. Starting the first probe woke both sleeping workers;
  stopping one workspace left the other running. Both disposable workspaces were deleted after
  their checks; the workers later became NotReady after the workspaces were stopped.
- The changing home public IP remains allowed at the user's request; a live test reached the UniFi
  management interface through it. The new GitOps resources were initially applied directly;
  verify ArgoCD has adopted them when reconciling the committed configuration.
