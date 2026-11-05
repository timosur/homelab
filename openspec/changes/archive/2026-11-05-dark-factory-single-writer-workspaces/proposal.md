# Proposal

## Why

Kata's shared filesystem does not forward advisory locks between VMs, and the existing SMB
workspace mount does not preserve the file modes needed by Git. These failures block safe
Dark Factory agent execution and cannot be fixed by changing the SMB mount alone.

## What Changes

- **BREAKING:** Use isolated directories on a dedicated Synology NFS export with enforced
  single-pod ownership instead of SMB or one iSCSI LUN per task.
- Permit different tasks to run concurrently on either sandbox worker and reuse each task's PVC
  and NFS directory across its sequential runs.
- Allow ten retained task PVCs within the existing 200Gi requested-storage budget while keeping
  the concurrent sandbox quota at two; retained workspaces must not consume run slots.
- Use the current upstream control plane's task-volume retention and reset rather than a separate
  cleanup controller: retain the defaults of seven days after the last run for completed or
  cancelled tasks and 30 days idle for any task. Cleanup discards unpushed work, caches, and local
  sessions; later runs recreate the workspace.
- Upgrade the pinned upstream version to one with task-volume retention and plain-Pod sandboxes,
  after verifying matching immutable images and compatibility with the existing safety checks.
- Assume stable workers and normal Kubernetes teardown. A surviving writer after node failure or
  forced deletion is outside the workspace guarantee; no host-level fencing is required here.
- Accept that upstream's delayed name-only PVC deletion can delete a replacement PVC and its data.
- Preserve pre-existing incompatible workspaces without automatic migration, deletion, or modification.
- Enforce `fsGroupChangePolicy: OnRootMismatch` and verify normal remounts preserve exact file
  modes, Git behavior, symlinks, and same-VM locks before any real agent runs. Accept that a
  mismatched or uninspectable workspace root can trigger recursive ownership and mode changes;
  do not implement a separate fail-closed root preflight.
- Build an amd64-only, credential-free project image for `timosur/homelab`, based on the released
  Claude project image. It supplies Python/uv, Node.js/OpenSpec, Helm, kubectl/Kustomize and Ansible;
  pull requests validate it without publishing, while `main` publishes immutable SHA and `latest`
  tags to GHCR. Pin its published digest before any task uses it.
- Extend fail-closed admission and normal task-volume handoff, and provision a dedicated NFS CSI service for the
  controller and both sandbox workers.
- Make Kata-compatible Cilium networking durable in Ansible: use a values file for both `eth+`
  and `en+` devices, preserve host-only socket load balancing, and roll out agents on config changes.

## Capabilities

### New Capabilities

- `dark-factory-task-workspaces`: Task workspace provisioning, exclusive ownership, recovery,
  filesystem semantics, and safe operation across both sandbox workers.

### Modified Capabilities

None.

## Impact

The change affects Dark Factory chart values, sandbox admission and power coordination, NFS CSI
onboarding, operational documentation, and live acceptance tests. It must remain compatible
with the pinned upstream control plane's repeated server-side apply of task PVCs. Existing task
volumes and the retained test volumes are outside automatic migration and cleanup.
