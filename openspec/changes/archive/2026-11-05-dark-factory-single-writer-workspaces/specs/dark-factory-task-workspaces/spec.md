# Spec Delta

## Purpose

Keep Dark Factory task workspaces isolated across both sandbox workers during normal Kubernetes
operation while preserving filesystem behavior and preventing simultaneous writers to a task's data.
Node failure, force deletion, a surviving VM after pod deletion, and a delayed name-only deletion
that targets a replacement PVC are outside this guarantee. The latter can delete replacement data.
Exact mode preservation is also not guaranteed when the workspace root does not match kubelet's
`OnRootMismatch` check or cannot be inspected.

## ADDED Requirements

### Requirement: Exclusive task workspace provisioning
The system SHALL provision each new task workspace under the designated dedicated NFS export
storage using `ReadWriteOncePod`, and SHALL reject incompatible task workspace claims rather
than silently using a less restrictive access mode or storage class.

#### Scenario: New task workspace
- **WHEN** the upstream control plane creates a task PVC using its supported `ReadWriteOnce` request
- **THEN** the persisted PVC uses `ReadWriteOncePod` and the designated NFS storage class

#### Scenario: Repeated upstream reconciliation
- **WHEN** the control plane reapplies its original task PVC specification
- **THEN** reconciliation succeeds without changing the persisted single-pod access mode

#### Scenario: Incompatible existing claim
- **WHEN** an existing task claim uses SMB or an incompatible access mode
- **THEN** the system blocks its use and does not automatically alter, delete, or migrate its data

### Requirement: Single active owner
Under normal Kubernetes operation, the system SHALL allow at most one sandbox to own a task
workspace at a time, including when multiple candidate pods target the same worker. Distinct task
workspaces SHALL remain independently usable while the configured worker pool is healthy.

#### Scenario: Two candidates for one task
- **WHEN** a second sandbox requests a workspace already owned by a sandbox
- **THEN** the second sandbox cannot mount or use that workspace

#### Scenario: Two independent tasks
- **WHEN** two tasks with distinct workspaces run while both workers are healthy
- **THEN** both runs can proceed concurrently on the designated workers

### Requirement: Durable run ownership
The system SHALL durably track each run's workspace identity and serialize normal handoff of a
task's PVC between runs. It SHALL fail closed when Kubernetes API results or ownership state are
unavailable or inconsistent.

#### Scenario: Coordinator restarts
- **WHEN** the coordinator restarts with an unfinished run
- **THEN** the recorded ownership remains available and prevents concurrent use of the workspace

#### Scenario: Normal completion
- **WHEN** Kubernetes reports that the previous run's pod is gone
- **THEN** the next run can receive ownership of the same task workspace

#### Scenario: Teardown cannot be checked
- **WHEN** Kubernetes API results are unavailable or inconsistent
- **THEN** the next run remains blocked

### Requirement: Fail-closed admission and binding
The system SHALL prevent a task workspace or sandbox from bypassing the single-writer policy
through normal PVC creation, reconciliation, pod creation, gate removal, or binding operations.
It SHALL fail closed when required admission, ownership state, or Kubernetes API checks are unavailable.

#### Scenario: Admission unavailable
- **WHEN** the workspace admission service is unavailable during a protected operation
- **THEN** the operation is rejected or remains blocked

#### Scenario: Binding without ownership
- **WHEN** a sandbox attempts to bind without a valid durable workspace reservation
- **THEN** the binding is rejected

### Requirement: Workspace filesystem semantics
The system SHALL enforce `fsGroupChangePolicy: OnRootMismatch` for managed sandbox pods and SHALL
preserve exact file modes across remounts when the workspace root satisfies kubelet's check. It
SHALL preserve symlinks, Git executable-bit tracking, and same-VM advisory lock behavior during a
run and across its remounts. The system SHALL NOT claim cross-VM advisory locking as a capability
of the shared filesystem.

#### Scenario: File modes survive remount
- **WHEN** a run creates files with modes `0755` and `0644` and its workspace is remounted before cleanup with a root satisfying kubelet's check
- **THEN** both files retain their exact modes and Git tracks their executable bits correctly

#### Scenario: Root mismatch or failed inspection
- **WHEN** the workspace root does not satisfy kubelet's check or cannot be inspected during a mount
- **THEN** kubelet may recursively change ownership and modes, including broadening group access; exact mode preservation is not guaranteed

#### Scenario: Sandbox policy cannot be weakened
- **WHEN** a managed sandbox pod is created or updated
- **THEN** admission requires `fsGroupChangePolicy: OnRootMismatch` rather than silently permitting the default `Always` policy

#### Scenario: Symlink survives remount
- **WHEN** a run creates a symlink and its workspace is remounted before cleanup
- **THEN** the symlink and its target remain intact

#### Scenario: Advisory lock within one sandbox
- **WHEN** one process holds an exclusive `flock` or POSIX record lock inside the sandbox
- **THEN** a conflicting process in that sandbox cannot acquire the same lock

### Requirement: Both-worker readiness
The system SHALL support attaching task workspaces to both designated sandbox workers and SHALL
verify safe sequential relocation between them before enabling real agents.

#### Scenario: Workspace moves between workers before cleanup
- **WHEN** a workspace is safely remounted on the other worker before its run is cleaned up and its root satisfies kubelet's check
- **THEN** its data and required filesystem semantics remain intact

### Requirement: Durable Kata service networking
Ansible SHALL configure Cilium with the string device filter `eth+,en+` and host-namespace-only
socket load balancing through a values file used for both install and upgrade. Helm-managed
configuration changes SHALL trigger agent pod recreation, without unconditional restarts on
unchanged playbook runs.

#### Scenario: Mixed worker NIC names
- **WHEN** workers use NIC names beginning with either `eth` or `en`
- **THEN** the configured device filter includes both naming families

#### Scenario: Kata-compatible configuration update
- **WHEN** Helm changes the Cilium ConfigMap
- **THEN** its DaemonSet pod template changes so recreated agents load the updated configuration

#### Scenario: Unchanged Cilium configuration
- **WHEN** the chart renders the same configuration on a subsequent run
- **THEN** the configuration checksum does not change

### Requirement: Credential-free homelab project image
The system SHALL build a credential-free `linux/amd64` project image for the homelab repository
from the released Claude project image. It SHALL run as the upstream non-root identity and include
the repository's Python/uv, Node.js/OpenSpec, Helm, kubectl/Kustomize, and Ansible validation
tools. Pull requests SHALL validate the image without publishing it; `main` SHALL publish immutable
SHA tags and `latest`. A project SHALL use a resolved immutable digest, not `latest`.

#### Scenario: Published project image
- **WHEN** a reviewed image change reaches `main`
- **THEN** GitHub Actions publishes the amd64 image to the designated GHCR repository with a SHA tag
  and `latest`, without embedding credentials

#### Scenario: Pull request image check
- **WHEN** a pull request changes the image definition or its workflow
- **THEN** CI builds and validates the image without publishing a runnable tag

#### Scenario: Agent project selection
- **WHEN** a Dark Factory project is configured for `timosur/homelab`
- **THEN** it references the published project-image digest and never the fake-agent image or mutable `latest`

### Requirement: Independent workspace and run capacity
The system SHALL allow ten retained task PVCs within a 200Gi requested-storage quota while
limiting concurrent sandbox pods to two. Retained workspaces SHALL NOT consume sandbox pod slots.
Requested PVC capacity SHALL NOT be described as enforced per-directory NFS storage capacity.

#### Scenario: A new task follows two retained workspaces
- **WHEN** two completed runs have retained 20Gi task PVCs and no sandbox pods are running
- **THEN** a third task can provision its workspace without exceeding the PVC count or storage quota

#### Scenario: Concurrent run limit remains unchanged
- **WHEN** two sandbox pods exist despite spare retained-workspace capacity
- **THEN** the pod quota still prevents a third sandbox pod

### Requirement: Task workspace retention
The system SHALL reuse a task's workspace across sequential runs and use upstream task-volume
retention and reset to delete eligible managed workspaces. Deletion SHALL NOT follow sandbox-controlled
links outside the intended directory or automatically remove incompatible old volumes.

#### Scenario: Follow-up run before cleanup
- **WHEN** a run ends and a subsequent run starts before the task workspace is removed
- **THEN** the subsequent run receives the same task PVC and workspace after normal handoff

#### Scenario: Completed or cancelled task retention
- **WHEN** a completed or cancelled task has no non-terminal run and its last run ended more than seven days ago
- **THEN** upstream retention can delete its managed PVC and directory during the hourly scan

#### Scenario: Idle task retention
- **WHEN** any task has no non-terminal run and its last run ended more than 30 days ago
- **THEN** upstream retention can delete its managed PVC and directory during the hourly scan

#### Scenario: Workspace reset
- **WHEN** an authorized user resets a task workspace and upstream permits the reset
- **THEN** its managed PVC and directory are deleted and unpushed work and local sessions are lost

#### Scenario: Follow-up run after cleanup
- **WHEN** retention or reset has removed the task workspace and a subsequent run starts
- **THEN** it gets a fresh PVC UID and NFS directory and may reconstruct only from available Git state

### Requirement: Existing data and staged rollout
The system SHALL preserve pre-existing incompatible workspaces pending explicit migration or
cleanup. Real agents and automatic sleep SHALL remain disabled until the new storage, admission,
two-worker acceptance checks, and separate power-sleep safeguards pass.

#### Scenario: Existing workspace requires migration
- **WHEN** an operator encounters an incompatible existing task workspace
- **THEN** its data and backing volume remain unchanged pending an explicit migration decision

#### Scenario: NFS export access
- **WHEN** the dedicated NFS export is configured
- **THEN** network access is restricted to the CSI controller and the two sandbox workers

#### Scenario: Acceptance is inconclusive
- **WHEN** any required workspace or power safety check fails or is inconclusive
- **THEN** real agents and automatic sleep remain disabled
