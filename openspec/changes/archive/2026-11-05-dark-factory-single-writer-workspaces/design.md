# Design

## Context

See `proposal.md` for the observed SMB and Kata failures. The selected upstream release is
`v2026.11.1`, commit `e42d4da438ec8ddbe5d7bc82403f3e0a7a0add2d`. It uses plain Pods and
task-volume retention; its task claims still request `ReadWriteOnce` and are reapplied with
server-side apply. `ReadWriteOnce` permits multiple pods on one node; the bundled virtiofsd does
not forward advisory locks between VMs. The release workflow built versioned images from the
release commit and the released chart pins its harness digest. The image registry does not provide
an independent source attestation; image interoperability remains unverified. The user skipped
the proposed isolated migration test and authorized migration of the new database. The proxy is
scaled to zero; the NFS driver's two-worker storage checks passed.

## Goals / Non-Goals

**Goals:** Enforce one workspace owner per task under normal Kubernetes operation, preserve exact
file modes and Git behavior when the workspace root satisfies kubelet's `OnRootMismatch` check,
support different tasks on both workers, and reuse the task workspace across sequential runs until
upstream retention or an authorized reset deletes it.

**Non-Goals:** Guarantee workspace safety after node failure, force deletion, a VM continuing
to write after Kubernetes reports its pod gone, a delayed name-only deletion targeting a
replacement PVC; guarantee exact file modes after a root mismatch or failed root inspection;
implement host-level workspace fencing or cross-VM advisory locks; automatically migrate existing
volumes; weaken Kata isolation; reset retained state as recovery; or implement a separate
task-volume cleanup controller. Existing power-sleep safeguards remain separate and unchanged.

## Decisions

### Dedicated NFS workspaces and admission

Deploy the Kubernetes NFS CSI driver and a dedicated StorageClass backed by the dedicated Synology
export, not the existing application share. Dynamically provision a distinct directory per PVC
generation; sequential runs for a task reuse the same PVC and directory.
Restrict NAS export access to the CSI controller and the two sandbox workers, and constrain the
controller's placement so this access list remains accurate. Review NFS authentication, root
squash, UID/GID behavior, and cleanup permissions; do not rely on the export as a security
boundary between privileged cluster hosts.
The user selected `No mapping` for all three approved NFS clients (controller, desktop, and GPU)
after a root-squashed Kata test failed to change file modes. AUTH_SYS preserves client root's
privileges on the export; all three clients must therefore be trusted. Do not broaden the client list.
The operator reports a 500 GiB shared-folder quota is set. It caps aggregate share use; individual
PVC sizes are not enforced by the NFS CSI driver.

Configure new task PVCs for that StorageClass. Extend the existing fail-closed admission service
to translate the pinned upstream's
`ReadWriteOnce` task-claim requests to `ReadWriteOncePod` on both creation and reconciliation.
Validate the persisted storage class and access mode, reject incompatible existing PVCs based on
their old state, and never mutate unrelated claims. Preserve namespace and instance scoping.

Server-side apply invokes an update path and may carry field ownership or immutable-field
constraints. Verify first and repeated applies against a real API server; if the transformation
cannot be made compatible without weakening validation, stop rather than silently fall back to
`ReadWriteOnce`. The alternative of forking and rebuilding the upstream provider was rejected for
this change, but remains an option if compatibility cannot be established.

### Durable Kata-compatible Cilium configuration

On 2026-11-05 the running Cilium agents retained `bpf-lb-sock-hostns-only=false` despite the
ConfigMap setting being true. Kata traffic reached egress policy before service translation and
was denied; direct backend pod connections succeeded. Recreating an agent exposed a second live
configuration fault: `devices=eth+` excluded the desktop's `enp30s0` NIC and prevented startup.
The live repair used `devices=eth+,en+` and recreated both sandbox-worker agents and the GPU agent.

Persist these choices in `ansible/roles/cilium/files/values.yaml`, copied to
`/etc/cilium/values.yaml` on the control-plane host and supplied to both Cilium install and upgrade.
Use the string `devices: eth+,en+`, `socketLB.hostNamespaceOnly: true`, and
`rollOutCiliumPods: true`. The chart's configuration checksum will recreate agents when its
managed ConfigMap changes, without unconditional restarts on every playbook run. Preserve all
other CLI settings. Validate the installed 1.18.3 chart's ConfigMap values and DaemonSet checksum
behavior locally; do not rerun the cluster playbook or trigger another live rollout for this edit.
Local validation passed: the 1.18.3 chart renders the intended ConfigMap settings and its
`cilium.io/cilium-configmap-checksum` remains stable for unchanged configuration and changes when
the device setting changes. Two regression tests verify the values and role wiring; the cluster
playbook syntax check passes. No playbook deployment or live rollout was performed for this edit.

### Run ownership and normal teardown

Add durable per-workspace ownership coordinated with pod gates and both binding APIs. Before
releasing a gate, reserve the workspace for the specific pod UID and the entire eligible worker
pool; let Kubernetes select the node normally. Record workspace and pod identities and use
UID/resource-version preconditions and atomic transitions; protect reservation updates from
untrusted identities. Under the stable-worker assumption, Kubernetes reporting the prior plain Pod
gone is the normal handoff signal; release its reservation before authorizing the next run to use
the same task PVC. Missing or inconsistent state and API errors block handoff; do not assume that
an unobserved operation succeeded. Update monitoring and RBAC to avoid relying on the removed
Sandbox CRD.

This signal is not proof that an unreachable or force-deleted VM cannot write. Those failure modes
are explicitly outside the workspace guarantee for now. The existing power guard's `fence` is not
a VM-termination proof, and the separate sleep-safety checks remain required before enabling sleep.

Binding admission remains side-effect-free and validates the existing reservation; admission dry
runs must not mutate durable ownership. Verify scheduler bindings, pre-bound pods,
legacy binding APIs, service-account permissions, PVC update paths, and unavailable API/webhook
behavior. Administrative bypass and deliberate deletion of trusted policy remain outside the
guarantee and must be documented.

Require the pod UID on `pods/binding`; the pinned Kubernetes scheduler supplies it, and the API
server enforces it as a binding precondition. Deny the legacy `bindings` endpoint outright: the
tested API server strips that UID before admission, so a webhook cannot safely validate it there.

### Task-volume retention and reset

Keep retained workspace capacity separate from concurrent run capacity: configure ten PVCs,
20Gi per workspace, and the existing 200Gi requested-storage budget, with at most two sandbox
pods. The previous two-PVC limit blocked HOME-4 despite zero running sandboxes. These request
quotas do not enforce per-directory NFS usage. The user authorized deleting the unused managed
`task-home-2` and `task-home-3` claims on 2026-11-05, including their backing data, without
clearing interlock records or deleting any newer task workspace. Verify claim generations,
absence of pod users, and normal admission authorization before deletion; verify CSI cleanup.
Both old claim/PV generations were removed through UID-guarded requests under the authorized
control-plane service-account identity; CSI logged removal of both NFS subdirectories. Their
interlock records remain intact. The pinned chart's quota template renders ten PVCs, two pods,
and 200Gi, matching the live quota. HOME-4 provisioned a new 20Gi PVC and reached an agent session,
but its run ended failed; the quota repair does not establish task execution success.

Use upstream retention and reset instead of a separate cleanup controller. With the accepted
defaults, its leader scans hourly and deletes volumes with no non-terminal run after seven days
since the last run for completed/cancelled tasks, or after 30 idle days for any task. An authorized
workspace reset also deletes a volume when upstream permits it. These operations discard unpushed
work, caches, and local sessions; a later run creates a fresh PVC and directory. The upstream
serializes its cleanup decision against run starts with the task-row lock.

Adapt admission to permit authorized upstream deletion of compatible managed NFS claims; preserve
old incompatible claims and unrelated volumes. The existing prototype's blanket protected-PVC
DELETE denial must not be deployed as the final policy because it blocks upstream cleanup. Verify
normal Pod/PVC protection, API outages, delayed deletion and same-name replacement behavior before
activation; do not assume a successful asynchronous DELETE means backend cleanup has completed.
Verify CSI cleanup cannot follow workspace-controlled symlinks, escape the intended directory, or
remove another workspace or the export root.

Upstream deletes by PVC name without a UID precondition. An isolated API-server test showed that a
delayed delete can mark a newly recreated PVC of the same name for deletion. The user accepts this
data-loss risk for now; admission must not claim to provide the missing generation precondition.

### Filesystem behavior

The Synology test preserved executable bits but kubelet's default `fsGroup` handling changed
`0755` to `0775` on remount. Require `fsGroupChangePolicy: OnRootMismatch` for sandbox pods using
the upstream non-root UID/GID, and enforce it through admission. Test exact preservation of `0755`,
`0644`, more restrictive modes, symlinks, and Git modes across repeated normal remounts on both
nodes when the workspace root satisfies the kubelet check. Subsequent runs reuse the task workspace
until retention or reset removes it. Do not treat Git's executable bit alone as sufficient.

The user accepts that a mismatched or uninspectable workspace root can trigger recursive ownership
and mode changes, potentially making restrictive files group-accessible. No separate trusted
root-mismatch preflight is required; exact mode preservation is not guaranteed in that situation.
This exception does not weaken the single-writer policy or the remaining rollout gates.

For the currently deployed Kubernetes 1.36.1, the kubelet's writable-volume root check requires
the expected GID, a directory with at least `0770` permissions and the setgid bit. A failed stat
also causes the recursive walk; `OnRootMismatch` is therefore neither a preflight nor fail-closed.
The upstream pod does not set `fsGroupChangePolicy`, and the NFS CSIDriver advertises `File`.
An init container is too late to protect the initial mount. Observed root modes are not a durable
guarantee; enforcing `OnRootMismatch` limits recursive changes but does not prevent them.

### Homelab project image

Use a project image derived from released `claude-project:2026.11.1`, not the fake ACP test image.
The upstream base already provides the non-root UID/GID 1000, Git, Bash, Curl, CA certificates and
OpenSSH client. The derived image adds only this repository's validation tools: Python 3 and uv,
Node.js 24 and OpenSpec, Helm, kubectl with Kustomize, and Ansible. It must not contain Kubernetes,
Azure, GitHub, SSH, OIDC, or other credentials; sandbox credentials continue to be injected through
the established mechanisms.

Publish only `linux/amd64`, matching both designated workers, to
`ghcr.io/timosur/homelab-dark-factory-project`. Pull requests build and validate but do not publish.
Pushes to `main` publish an immutable `sha-<commit>` tag and mutable `latest`; resolve and pin the
immutable digest in the Dark Factory project before an agent runs. Do not select `latest` in a
project configuration. A separate fake-agent image remains exclusively for the controlled first-run
test.

### Alternatives

SMB or NFS through the same virtiofsd cannot provide the requested cross-VM advisory locks.
RWX plus lockfile-only coordination was rejected because general `flock` and record-lock users
would remain unsafe. Plain `ReadWriteOnce` was rejected because it permits multiple pods on the
same node. Per-task `ReadWriteOncePod` preserves the upstream sequential-run model while allowing
separate tasks to run concurrently.

## Risks / Trade-offs

- [CSI attachment or node availability delays a task] → Keep the workspace gated and surface the
  reason; do not automatically force-detach volumes.
- [Upstream server-side apply restores `ReadWriteOnce`] → Admission must be fail-closed and tested
  with the exact pinned provider; do not activate if compatibility is unproven.
- [A VM survives node failure or force deletion] → A stale writer may conflict with cleanup or a
  later run. This failure mode is accepted as outside the workspace guarantee, not solved by RWOP.
- [Admission bypass is incomplete] → Keep agents disabled until the normal-operation policy passes.
- [Kubelet changes modes recursively] → Enforce `OnRootMismatch` and test exact modes in normal
  remounts; the user accepts recursive ownership and permission changes if the root does not match
  or cannot be inspected. Do not describe this policy as fail-closed for filesystem modes.
- [NFS export or CSI permissions are too broad] → Restrict the dedicated export to the controller
  and workers, verify the NFS authorization model, and treat the authorized cluster hosts as trusted;
  `No mapping` permits their root processes root-level access to the export.
- [A delayed name-only delete hits a replacement PVC] → This can delete newer workspace data and
  is explicitly accepted for now; record the risk and do not present deletion as UID-guarded.
- [Cleanup escapes its directory] → Test symlinks and asynchronous deletion, preserve incompatible
  old volumes, and do not activate if the required isolation checks are inconclusive.
- [Run ends with unpushed work] → The user explicitly accepts loss when the workspace is removed;
  communicate the outcome and never imply it was recovered.
- [Existing retained data is mistaken for a managed run workspace] → Preserve old PVCs, PVs,
  LUNs, and shares; require separate explicit authorization for migration or cleanup.
- [Project image receives credentials or changes unexpectedly] → Build from the released base,
  exclude credentials, validate on pull requests, publish only from reviewed `main`, and pin a
  digest rather than a mutable tag before assigning it to a project.

## Migration Plan

1. Verify the selected upstream revision and matching image provenance; adapt Pod admission,
   monitoring, task handoff and retention compatibility without enabling sleep settings.
2. Enable Synology NFS and create the dedicated export with the approved access restrictions.
   Deploy the NFS CSI driver and wait for both node plugins and its controller. Before enabling
   any sandbox, deploy the verified proxy image and verify admission TLS/CA and required permissions.
   Preserve manual sync ordering.
3. For the requested early live UI, first sync prerequisites. The release chart's values schema
   rejects zero control-plane and gateway replicas, so bootstrap the chart's own sandbox namespace
   and zero-pod/zero-PVC quota before the chart sync; verify the API server enforces both limits.
   Then sync the chart with its normal schema validation and let it adopt the same namespace and
   quota. Tasks cannot provision until the remaining workspace, admission and power gates pass; do
   not enable worker labels or automatic sleep. Restore normal quotas only after the staged checks.
4. Use newly created, clearly identified test claims to validate single-pod exclusion, normal
   exact-mode preservation with a matching root, Git/symlinks, repeated upstream applies, safe
   node-to-node remount, upstream cleanup, stale cleanup attempts, and API failures.
5. Keep pre-existing SMB or incompatible PVCs unchanged. Plan any data migration separately.
6. Only after all workspace and original power/isolation checks pass, run a fake agent followed by
   a human-reviewed documentation-only task. Automatic sleep stays disabled if any check is unclear.

Rollback stops new runs and preserves existing PVCs, PVs, LUNs, durable ownership and interlock
state. Do not roll back admission or storage enforcement while a protected workspace can still be
in use; resolve any interrupted cleanup before allowing another claim generation.
