# Dark Factory pilot

Dark Factory replaces the proposed Paperclip deployment. The GitOps applications for the
controller, CSI driver, database prerequisites, chart, and wake permissions intentionally require
manual sync. The user authorized an early UI sync only with sandbox pod and PVC quotas set to zero;
do not start a real task until the gates below are satisfied.

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

The intended sandbox workers are `homelab-amd-desktop` and `homelab-gpu`. The default RuntimeClass
supports both labeled workers, while the two-worker proxy configuration remains commented out.
The existing Ollama proxy can suspend the GPU independently of sandbox activity. Do not schedule
sandboxes on the GPU until both workers' power paths and the pool interlock are validated. Installing
Kata alone does not enable GPU scheduling.
The pool coordinator treats both workers as one conservative pool: it must fence both hosts and
observe a fresh lease from each before opening sandbox gates, and sandbox or proxied activity keeps
both hosts awake. Either worker being unavailable blocks new sandboxes. Code and placement are
unverified on the live cluster; do not start sandbox workloads until the staged checks pass.

Never delete, recreate, or reset the `dark-factory-interlock` ConfigMap or host guard state as a
routine recovery step. An uncertain gate PATCH or an abandoned HTTP reservation stops takeover;
an uncertain host suspend blocks its fence. Operator recovery must first fence the old controller,
resolve outstanding API and suspend operations, and inspect live workloads. A reboot or failed
suspend can require manual recovery; do not assume an SSH disconnect means suspension completed.

## Task workspace ownership and recovery

- The workspace guarantee assumes stable workers and normal pod teardown: the next run cannot
  reserve the same claim name until Kubernetes reports the previous pod UID absent. This is not
  fencing for a VM that survives node failure or force deletion. Do not treat a missing pod as
  proof that an unreachable worker stopped writing; isolate and inspect that worker before recovery.
- The interlock records claim UID, pod name and UID, and the complete worker pool. A claim recreated
  under the same name is a different generation, but a prior pod must still be gone before handoff.
  Records may outlive a PVC. Do not clear records to resolve a busy workspace, and do not assume a
  missing or unreadable record is safe. Restart in the ordinary idle phase preserves the record;
  restart during an uncertain release or API failure requires operator investigation.
- Restrict the interlock ConfigMap, its Role/RoleBinding, sandbox admission configuration and
  worker labels to trusted administrators and the intended controller service account. The local
  Role grants the `wol-proxy` service account `get` and `update` on the named interlock; it does
  not establish that other cluster-wide grants are absent. Review effective permissions before
  rollout. Privileged administrators, compromised authorized hosts and policy deletion are outside
  the workspace guarantee.
- Upstream's leader performs retention hourly. A board member can request an audited workspace
  reset through the UI; upstream rejects reset while a run is non-terminal. Both operations use
  the control plane's service account to delete the claim. Local admission additionally requires
  the authorized actor, compatible NFS claim identity, and a complete pod list with no claim user.
  A successful Kubernetes delete request is not proof the PV or CSI directory has been removed.
- After a timeout, API outage, or interrupted cleanup, stop new runs and preserve the interlock and
  all PVC/PV data. Inspect the old pod UID, claim UID, PV, deletion status, controller state, and
  CSI cleanup outcome before deciding on recovery. Upstream deletes by name without a UID
  precondition: a delayed request can delete a same-name replacement and its data, an explicitly
  accepted risk. Do not retry destructive operations or migrate incompatible volumes automatically.

## Prerequisites

1. Verify the actual K3s version is at least 1.36 and that image volumes work on both workers.
   Verify `cert-manager-csi` and its node plugin are healthy on `homelab-amd-desktop` and
   `homelab-gpu`. The selected upstream uses plain Pods, not the `agent-sandbox` CRD.
2. Verify KVM is enabled and `/dev/kvm` exists on both workers. From `ansible/`, run
   `ansible-playbook playbooks/dark-factory-kata.yml` after reviewing the playbook. It installs
   Kata 4.3.1 and **restarts each worker's K3s agent when changes require it**, one worker at a time.
   Use `--limit homelab-amd-desktop` or `--limit homelab-gpu` to prepare one worker at a time.
   It refuses to overwrite a pre-existing
    Kata installation, shim, or different containerd v3 template. Run a Kata test pod before
    deploying any agent; verify image volumes, cert-manager CSI volumes and task volumes on
    both workers. GPU test pods require safe power handling and explicit placement.
   If this playbook interrupted its own extraction, inspect the host before retrying. An operator
   may explicitly set `dark_factory_kata_adopt_verified_installation=true` to resume without
   overwriting `/opt/kata`; the role requires the checksum-pinned local archive and compares its
   complete contents with the extraction. Do not use this option to adopt an unrelated installation.
3. Generate a **dedicated** Ed25519 key for the `dark-factory-power` desktop account. Put the
  base64-encoded private key in Azure Key Vault as `wol-proxy-desktop-power-ssh-key` and the
  independently verified desktop SSH host-key line in `wol-proxy-desktop-known-hosts`. Supply
  the public key to the Ansible role as `dark_factory_power_authorized_key`; never put the private
   key in Git, command arguments, or logs. The key is copied into an in-memory volume at pod start;
   restart the proxy pod after rotating it. From `ansible/`, run
   `ansible-playbook playbooks/dark-factory-power-guard.yml --limit homelab-amd-desktop` with that public-key variable after
   reviewing its restricted account and sudo rule. The playbook also supports `homelab-gpu`;
   prepare each host separately with `--limit` and its own dedicated public key. The role manages
   the account's authorized keys exclusively, so do not rerun it with the other host's key.
   For the GPU, store a separate base64-encoded private key in `wol-proxy-gpu-power-ssh-key` and
   its independently verified SSH host-key line in `wol-proxy-gpu-known-hosts`. Supply only that
   public key to the GPU guard. Installing the guard alone does not make the existing Ollama suspend
   path safe. Restart the proxy after rotating either power key.
4. Configure an Authentik OIDC application with issuer
    `https://auth.timosur.com/application/o/dark-factory/`, client ID `dark-factory`, callback
    `https://factory.home.timosur.com/auth/callback`, and a `groups` claim containing
    `board-member` for authorized users. `scripts/setup_authentik_homelab.py --provision-oidc`
    can create a confidential provider/application using a short-lived in-cluster API token and
    write its client secret only to `dark-factory/dark-factory-oidc` under `client-secret`. It
    never prints the secret; copy it to Key Vault entry `dark-factory-oidc-client-secret`, then
    wait for ExternalSecret reconciliation. Add intended users to `board-member`. Verify users
    outside that group cannot access protected UI actions.
5. Provision these Azure Key Vault entries using the secret provider, never Git or shell history:
   `dark-factory-oidc-client-secret`, `dark-factory-github-token`, and
   `dark-factory-claude-oauth-token`. Use a fine-grained GitHub PAT limited to
   `timosur/homelab` with Contents, Pull requests and Issues read/write plus Metadata read; do
   not grant workflow or administration permissions. Use a dedicated revocable Claude subscription
   credential. The Claude credential is readable by sandbox code. Do not print secret values.
6. Sync `dark-factory-prerequisites`. Wait until its AppDBClaim, PostgreSQL runtime Role,
   ExternalSecrets, and generated owner/runtime connection Secrets are ready. The owner and app
   roles must be distinct; neither should have `CREATEROLE`. Verify database backup and restore.
7. Pin image digests in `apps/dark-factory/values.yaml` to upstream release `v2026.11.1`, source
   commit `e42d4da438ec8ddbe5d7bc82403f3e0a7a0add2d`. The successful upstream release workflow
   built from that commit, and the published chart pins the harness digest. The image registry
   does not provide an independent source attestation. Both amd64 and arm64 are present. The
   workspace renderer and PVC/Pod snapshots are unchanged from the previously tested candidate;
   its 13 existing SQL migrations are unchanged and one migration was added. The user skipped the
   proposed isolated migration test and authorized the live migration of a new database. Verify
   the migration result, image interoperability, and Git-proxy credential isolation; an old smoke
   check is not evidence for the released images.

## Staged installation

- Prepare `nfs-csi-driver` for manual sync only after reviewing its manifests. The dedicated
  `k3s_nfs` export is restricted to the controller and both workers. The operator reports a
  500 GiB shared-folder quota was set. This is an aggregate cap, not a reservation or per-task
  quota: the NFS CSI driver does not enforce individual PVC sizes. Never test against an existing
  SMB or iSCSI volume. The user selected NFS `No mapping` for those three clients: their root
  processes retain root-level access to the dedicated export. Do not add other clients.
- Sync `dark-factory` only after all prerequisites are ready. For the authorized early UI, bootstrap
  the release chart's sandbox namespace and `dark-factory-sandbox-quota` with both `pods` and
  `persistentvolumeclaims` set to zero, then verify the API server rejects new pods and PVCs. The
  chart's schema rejects zero control-plane and gateway replicas, so this bootstrap must happen
  before syncing the chart; let the chart adopt the same namespace and quota. Tasks submitted while
  the quota is zero cannot provision. Do not add worker labels or enable sleep. The upstream Helm
  migration hook runs as an ArgoCD `PreSync` hook; placing the database/secret resources in the same
  sync would not ensure they exist before migration. Check the migration job, UI, gateway, and
  private CA. Restore the normal quotas only after the remaining acceptance gates pass.
- After the chart creates `dark-factory-sandboxes`, sync `dark-factory-wake`. It creates the TLS
  certificate, fail-closed admission webhooks and namespace-scoped pod patch permission. The role
  also permits listing pods and reading the two named worker nodes; it grants no node mutation or
  Secret access. **Until the admission service is ready, these webhooks reject sandbox pod creation.**
- Publish the updated `wol-proxy` image, pin its verified digest in the deployment and verify the
  running pod uses that exact image before enabling any interlock settings. The deployment currently
  uses a mutable tag; publishing a tag alone does not guarantee a rollout. The existing auto-syncing
  application may restart when its deployment changes. Wait for all four worker ExternalSecrets,
  admission certificate and CA injection. Fence old proxy processes and outstanding SSH suspend
  requests on **both** hosts before enabling the pool; old images ignore `sandboxPool` and can still
  suspend the GPU through Ollama's legacy path. Enable sandbox monitoring, the complete commented
   `sandboxPool` configuration, and admission first, but **leave `sandboxPowerInterlockEnabled`
   commented out**. Verify
  server-side dry-run pod creation actually inserts the gate and the `OnRootMismatch` fsGroup
  policy, `nodeName` and both binding APIs cannot bypass the gate, unauthorized gate
  removal fails, and admission outages reject pod creation.
  In this staging mode sandbox pods stay gated: only the power coordinator can reserve and release
  a gate. Do not start a real task until the coordinator is enabled and validated.
- Before enabling `sandboxPowerInterlockEnabled`, revoke the legacy SSH keys' direct suspend
  authority on both hosts; assess other uses of those keys first. Verify both dedicated power
  accounts can run only the guard's `fence`/`suspend` operations and their known-hosts entries
  match their respective hosts. No unfenced automation may suspend either worker while the
  interlock is active.
- During a controlled test window, enable `sandboxPowerInterlockEnabled` and exercise idle sleep,
  wake on new sandbox, two concurrent
  sandboxes, active proxied HTTP, webhook/API outages, controller restart during suspend, delayed
  SSH requests on each host, failed wake of either host, Ollama streaming, and cleanup. Confirm a
  fresh kubelet lease follows each host fence and neither gate opens until both renew.
  Leave automatic sleep disabled if any check is inconclusive; the home cluster shares the desktop
  with other workloads.
- After these checks, label **only** the two verified workers
  `dark-factory.home.timosur.com/sandbox-worker=true`. The default RuntimeClass selects this label
  and tolerates both workers' taints; without it, sandboxes remain unscheduled. Restrict changes
  to this label and the admission policy to trusted administrators; test placement on each worker
  with fake workloads before any real agent.
- Check Authentik login and the home-only route. Configure credential references to
  `dark-factory-github` and `dark-factory-claude` in the UI, and configure the GitHub project for
  `https://github.com/timosur/homelab`. Build the credential-free, amd64-only
  `ghcr.io/timosur/homelab-dark-factory-project` image from the released
  `claude-project:2026.11.1` base. Pull requests validate it without publishing; `main` publishes
  `sha-<commit>` and `latest`. The first published SHA tag resolves to
  `sha256:5f5c70cb625703f4422afbd40cf1b3053b63fec9803180645e17cf01e9bac1ae`; configure
  the project with `ghcr.io/timosur/homelab-dark-factory-project` at that immutable digest, never
  `latest`. The released upstream base was observed at digest
  `sha256:c9491848a9c48475636288f37b6d46a680bf72ede21eeb5e262b89121a4d4b6b`.
- Verify branch protection requires human review, prevents direct pushes and PAT bypass, and
  prohibits agent merges. Review PR-triggered workflows. The Git proxy is not a substitute for
  repository branch protection.

## Acceptance and operations

- **Historical blocking test (2026-11-03):** with Kata 4.3.1 and `homelab-smb`, conflicting
  `flock` and POSIX record locks were granted across the two sandbox VMs. The same lock tests
  blocked correctly between ordinary pods on the same workers; a Kata pod also acquired locks
  held by a normal pod. Git-style exclusive lockfile creation did block across nodes. Do not run
  real agents until the NFS single-pod policy and same-VM lock behavior pass. Cross-VM advisory
  locking is not claimed; independent tasks must still be tested concurrently.
- The same test observed `0777` filesystem modes and Git `core.filemode=false`; a file chmodded
  executable was committed as mode `100644`. Do not assume POSIX modes or executable-bit changes
  survive on this storage; resolve and retest before relying on them.
- A separate `homelab-iscsi` test with `ReadWriteOncePod` blocked a second pod on the same node,
  supported non-root writes and symlinks, and recorded executable and non-executable Git files
  correctly. Locks worked between processes within one Kata VM. Kubelet `fsGroup` handling changed
  a file's mode from `0755` to `0775` on remount. This is a candidate single-writer workaround,
  not cross-VM lock support: it still requires an enforced one-pod-per-task claim, GPU CSI support,
  cross-node remount tests, and an explicit storage design decision. The pinned upstream currently
   renders task PVCs with `ReadWriteOnce`, which does not prevent multiple pods on the same node.
- **NFS/Kata blocking test (2026-11-03):** the dedicated CSI driver provisioned a disposable
  `ReadWriteOncePod` claim and blocked competing pods on both the same and the other worker. A
  Kata pod running as UID/GID 1000 saw its workspace root as `1025:100`, not the required group
  1000, and `chmod` on its own file returned `Operation not permitted` on both workers. A normal
  pod using the same claim could change the mode. A same-VM lock test was inconclusive because a
  second process could not reopen the test file for writing. The test claim and its CSI directory
  were removed after the test pods stopped. Do not run agents until permissions, normal remounts
  with a matching root, Git, and same-VM locking pass on both workers under the final policy.
- A subsequent disposable desktop test with NFS `No mapping` preserved `0755`, `0644`, `0600`,
  `0700`, a symlink, and Git executable-bit tracking after remount; same-VM `flock` and record locks
  blocked competing processes. The controller successfully deleted the test directory while its own
  rule still mapped root to guest. The user subsequently chose `No mapping` for all three clients;
  GPU and cross-node acceptance remain outstanding under that final policy.
- Under the final policy, a GPU Kata pod could change file modes and passed same-VM `flock` and
  record-lock checks. Two independent claims ran concurrently on the GPU; deleting a claim with
  links to the export root and another workspace left the other workspace's marker intact. Its
  remounted Git test recorded executable and ordinary files correctly. The desktop became
  unreachable before the two-worker and cross-node checks could then be run. Both test claims,
  their CSI directories, and the temporary RuntimeClass and namespace were removed.
- After the desktop returned, two new `ReadWriteOncePod` claims ran concurrently in Kata on
  different workers. Competing pods on the same and opposite worker remained unscheduled with the
  single-pod claim conflict. Same-node remounts and both desktop→GPU→desktop and
  GPU→desktop→GPU round trips preserved `0755`, `0644`, `0600`, `0700`, symlinks, Git index modes,
  and writes made on the other worker. Same-VM `flock` and record-lock checks passed on both nodes.
  The root had GID 1000 and mode `2777` during these checks; this does not remove the accepted
  mismatch risk. A root-owned volume root was not accepted by Git as a repository directory, but
  an owned subdirectory worked. Pinned upstream source uses `/data/repo`; actual image behavior is
  still unverified.
- Deleting one of those new claims, which contained links to the export parent, an absolute root,
  and the sibling claim directory, left the sibling's marker, modes, and Git state intact after
  another cross-node remount. The export remained mountable. The NFS CSI `v4.13.6` delete path
  uses `os.RemoveAll` for the workspace and stops its empty-parent cleanup at the export mount;
  this test does not cover a surviving writer after node failure or force deletion. The approved
  test claims, CSI directories, pods, RuntimeClass, and namespace were removed.
- An isolated API-server binding test held an already-admitted request while deleting and recreating
  a fake unscheduled pod with the same name. The API server rejected the delayed request on its old
  pod UID and left the replacement unbound. Legacy binding was denied; unavailable test admission
  rejected binding. Local HTTP tests exercised the actual reservation verifier, including dry runs,
  unauthorized gate removal, stale ownership, and API failure without reservation writes. These
  tests do not replace the live staged admission and handoff checks.
- Run a fake-agent task first. Test default-deny egress, denied/approved hosts, sandbox identity,
  Git proxy ref restrictions, two-pod quota, wake failure, node readiness, and cleanup. Do not
  assume excess tasks queue successfully; verify their actual behavior.
- Verify new task PVCs use `dark-factory-nfs` with enforced single-pod ownership and work for Git,
  exact file modes on normal remounts with a matching root, symlinks, and same-VM locking. A
  storage-class change alone does not enforce single-pod ownership. The user accepts the
  surviving-writer risk after node failure or force deletion, and the risk that an upstream delayed
  name-only delete can remove a replacement PVC.
- **Filesystem gate still open:** the pinned upstream pod specifies `fsGroup: 1000` without
  `fsGroupChangePolicy`, and the live NFS CSI driver advertises `fsGroupPolicy: File`. Local
  admission now injects and validates `OnRootMismatch` for managed workspace pods; this has not
  been deployed. The user accepts that a mismatched or uninspectable root can still trigger
  recursive ownership and mode changes, potentially broadening group access. No root-mismatch
  preflight is planned, and exact modes are not guaranteed in that situation. Do not mount retained
  task data with the unmodified upstream policy; complete admission and both-worker normal-remount
  checks before running agents. An init container cannot check before volume setup.
- Run one real, documentation-only task. Confirm it opens a PR and a human alone merges it.
  Monitor CPU/memory, the two-hour run limit, and node availability.
- Task workspaces persist across sequential runs. Selected upstream retention defaults remove them
  after seven days for completed/cancelled tasks or 30 days idle for any task; an authorized reset
  can remove them earlier. Deletion discards unpushed work, caches, and local sessions. Preserve all
  pre-existing incompatible PVCs, PVs, and NAS volumes unless separately authorized.
- Back up the database before upgrades; the upstream project is pre-MVP and migrations may change.
  Image compatibility, live cluster behavior, and the automatic-sleep interlock remain unverified.

## Current staged state — 2026-11-04

- The manually synced `nfs-csi-driver` application is `Synced` and `Healthy`. Its controller runs
  on `homelab-amd`, its node plugins run only on the desktop and GPU, and `dark-factory-nfs` is a
  non-default StorageClass with `Delete` reclaim policy. The live PVC inventory contains no
  leftover Dark Factory test claim; pre-existing SMB, iSCSI, local-path, and other application
  volumes remain bound to their original StorageClasses.
- `dark-factory-prerequisites` is `Synced` and `Healthy`: `dark-factory-db`, its database role,
  and all three ExternalSecrets are Ready. The release `v2026.11.1` chart commit
  `e42d4da438ec8ddbe5d7bc82403f3e0a7a0add2d` is `Synced` and `Healthy`; its PreSync migration
  completed against the new database, which has 14 entries in `dark_factory._sqlx_migrations`
  through `20261103000002`. The release control plane and two gateways run on the released
  immutable control-plane digest. This is not a full agent-image interoperability test.
- The authorized early UI is live at `https://factory.home.timosur.com/` and redirects unauthenticated
  requests to login. Both sandbox quota dimensions remain zero: the API server rejects new sandbox
  pods and `ReadWriteOncePod` claims. Neither worker has the sandbox-worker label, `wol-proxy`
  remains scaled to zero, `dark-factory-wake` remains unsynced, and automatic sleep is disabled.
  Do not restore capacity, label workers, or run a task until the remaining gates pass.

## Rollback

Stop new runs and keep admission, the workspace reservations, and the power interlock in place
while any protected pod or cleanup operation may still be active. Inspect pod and claim UIDs, PVs,
CSI deletion status, and both workers; an unreachable worker is not proof its sandbox stopped.
Do not force-delete pods, reset interlock records, or revert to weaker admission or storage rules
to make a rollback proceed. Preserve existing PVCs, PVs, NAS data and unrelated shares. Resolve
uncertain operations and verify backups before any separately authorized migration or deletion.
