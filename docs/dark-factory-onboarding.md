# Dark Factory pilot

Dark Factory replaces the proposed Paperclip deployment. The GitOps applications for the
controller, CSI driver, database prerequisites, chart, and wake permissions intentionally require
manual sync. Do not sync the chart or start a real task until the gates below are satisfied.

## Automatic sleep safety gate

Automatic sleep is implemented but **not enabled or verified on the desktop**. The admission
webhook gates new sandbox pods; durable Kubernetes ownership serializes gate removal, proxied HTTP
activity and sleep; the desktop-side guard fences old suspend epochs. After a sleep attempt, the
controller advances the epoch before reopening gates and waits for a new kubelet lease renewal.

The host guard requires every desktop suspend to pass through it: an unrelated suspend could run
the post-sleep hook and falsely complete a pending request. Old proxy credentials, desktop idle
policies, and other automations must not retain an unfenced desktop-suspend path. Manual suspend,
power loss, privileged administrative bypass, and admission-policy deletion remain outside the
guarantee. Restrict changes to the admission webhooks and interlock ConfigMap to trusted cluster
administrators. The settings in
`apps/wol-proxy/configmap.yaml` are deliberately commented out until the checks below pass.

Never delete, recreate, or reset the `dark-factory-interlock` ConfigMap or host guard state as a
routine recovery step. An uncertain gate PATCH or an abandoned HTTP reservation stops takeover;
an uncertain host suspend blocks its fence. Operator recovery must first fence the old controller,
resolve outstanding API and suspend operations, and inspect live workloads. A reboot or failed
suspend can require manual recovery; do not assume an SSH disconnect means suspension completed.

## Prerequisites

1. Verify the actual K3s version is at least 1.36 and that image volumes work on the desktop.
   Sync `agent-sandbox` and `cert-manager-csi` in ArgoCD and verify their controllers and CSI
   node plugin are healthy on `homelab-amd-desktop`.
2. Verify KVM is enabled and `/dev/kvm` exists on that desktop. From `ansible/`, run
   `ansible-playbook playbooks/dark-factory-kata.yml` after reviewing the playbook. It installs
   Kata 4.3.1 and **restarts the desktop's K3s agent**. It refuses to overwrite a pre-existing
   Kata installation, shim, or different containerd v3 template. Run a Kata test pod before
   deploying any agent; verify image volumes, cert-manager CSI volumes and SMB task volumes.
3. Generate a **dedicated** Ed25519 key for the `dark-factory-power` desktop account. Put the
  base64-encoded private key in Azure Key Vault as `wol-proxy-desktop-power-ssh-key` and the
  independently verified desktop SSH host-key line in `wol-proxy-desktop-known-hosts`. Supply
  the public key to the Ansible role as `dark_factory_power_authorized_key`; never put the private
   key in Git, command arguments, or logs. The key is copied into an in-memory volume at pod start;
   restart the proxy pod after rotating it. From `ansible/`, run
   `ansible-playbook playbooks/dark-factory-power-guard.yml` with that public-key variable after
   reviewing its restricted account and sudo rule.
4. Configure an Authentik OIDC application with issuer
   `https://auth.timosur.com/application/o/dark-factory/`, client ID `dark-factory`, callback
   `https://factory.home.timosur.com/auth/callback`, and a `groups` claim containing
   `board-member` for authorized users. Verify unauthorized users cannot access the UI.
5. Provision these Azure Key Vault entries using the secret provider, never Git or shell history:
   `dark-factory-oidc-client-secret`, `dark-factory-github-token`, and
   `dark-factory-claude-oauth-token`. Use a fine-grained GitHub PAT limited to
   `timosur/homelab` with Contents, Pull requests and Issues read/write plus Metadata read; do
   not grant workflow or administration permissions. Use a dedicated revocable Claude subscription
   credential. The Claude credential is readable by sandbox code. Do not print secret values.
6. Sync `dark-factory-prerequisites`. Wait until its AppDBClaim, PostgreSQL runtime Role,
   ExternalSecrets, and generated owner/runtime connection Secrets are ready. The owner and app
   roles must be distinct; neither should have `CREATEROLE`. Verify database backup and restore.
7. Check and pin the image digests in `apps/dark-factory/values.yaml` together with the chart's
   source revision. The published `dev` images are mutable and do not identify their source
   revision in their image labels. Verify the exact selected images work together; otherwise stop
   and obtain compatible upstream artifacts. The only visible successful release workflow was
   run from `ac45d41be51762593278651e6a927aab1990f06b`, 107 commits before the pinned chart
   revision; this does not establish which commit produced the current `dev` tags. In particular,
   do not assume those images have the newer Git proxy or its credential isolation. The chart's
   default Claude harness tag does not exist. A local, network-isolated smoke check confirmed
   that the pinned arm64 control-plane image parses the rendered configuration and contains Git
   proxy code; it did not test the amd64 runtime, plugin interoperability, or credential isolation.

## Staged installation

- Sync `dark-factory` only after all prerequisites are ready. Its upstream Helm migration hook
  runs as an ArgoCD `PreSync` hook; placing the database/secret resources in the same sync would
  not ensure they exist before migration. Check the migration job, UI, gateway, and private CA.
- After the chart creates `dark-factory-sandboxes`, sync `dark-factory-wake`. It creates the TLS
  certificate, fail-closed admission webhooks and namespace-scoped pod patch permission. The role
  also permits listing sandboxes and reading the named desktop node; it grants no node mutation or
  Secret access. **Until the admission service is ready, these webhooks reject sandbox pod creation.**
- Publish the updated `wol-proxy` image and update its existing digest-pinned reference to the
  newly built image before enabling any interlock settings. Publishing a tag alone does not change
  the currently pinned image or restart the pod. Wait for
  both new ExternalSecrets, admission certificate and CA injection. Enable sandbox monitoring
  and admission first, but **leave `sandboxPowerInterlockEnabled` commented out**. Verify
  server-side dry-run pod creation actually inserts the gate, `nodeName` and both binding APIs
  cannot bypass it, unauthorized gate removal fails, and admission outages reject pod creation.
  In this staging mode sandbox pods stay gated: only the power coordinator can reserve and release
  a gate. Do not start a real task until the coordinator is enabled and validated.
- Before enabling `sandboxPowerInterlockEnabled`, stop and fence any old proxy process and
  outstanding SSH suspend request. Revoke its legacy desktop SSH key or remove its direct suspend
  authority; assess other uses of that key before changing it. Verify the dedicated power account
  can run only the guard's `fence`/`suspend` operations and that its known-hosts entry matches the
  desktop. No unfenced automation may suspend the desktop while the interlock is active.
- During a controlled test window, enable `sandboxPowerInterlockEnabled` and exercise idle sleep,
  wake on new sandbox, two concurrent
  sandboxes, active proxied HTTP, webhook/API outages, controller restart during suspend, delayed
  SSH requests, failed wake, and cleanup. Confirm a fresh kubelet lease follows each host fence.
  Leave automatic sleep disabled if any check is inconclusive; the home cluster shares the desktop
  with other workloads.
- Check Authentik login and the home-only route. Configure credential references to
  `dark-factory-github` and `dark-factory-claude` in the UI, and configure the GitHub project for
  `https://github.com/timosur/homelab`. Use an amd64 project image compatible with the selected
  harness; upstream `claude-project:dev` was observed at digest
  `sha256:b5029c1da745212ee1f38c2b96b77767c3d0bdccccd1896aab2fc73724f94fa1`.
- Verify branch protection requires human review, prevents direct pushes and PAT bypass, and
  prohibits agent merges. Review PR-triggered workflows. The Git proxy is not a substitute for
  repository branch protection.

## Acceptance and operations

- Run a fake-agent task first. Test default-deny egress, denied/approved hosts, sandbox identity,
  Git proxy ref restrictions, two-pod quota, wake failure, node readiness, and cleanup. Do not
  assume excess tasks queue successfully; verify their actual behavior.
- Verify the task PVC uses `homelab-smb` and works for Git, file modes, symlinks, and locking.
  SMB mounts use permissive mode options, so filesystem permissions are not a security boundary.
- Run one real, documentation-only task. Confirm it opens a PR and a human alone merges it.
  Monitor CPU/memory, the two-hour run limit, and node availability.
- Task workspaces persist for follow-up. Review and explicitly remove them when no longer needed.
  `homelab-smb` uses `Retain`: removing a PVC does not automatically delete the PV or NAS data.
- Back up the database before upgrades; the upstream project is pre-MVP and migrations may change.
  Image compatibility, live cluster behavior, and the automatic-sleep interlock remain unverified.
