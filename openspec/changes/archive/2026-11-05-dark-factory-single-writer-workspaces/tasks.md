# Tasks

## 1. Task claim enforcement

- [x] 1.1 Update fail-closed task-PVC admission tests for the NFS storage class, namespace and instance scoping, incompatible SMB/iSCSI claims, and unavailable admission; verify unrelated PVCs remain unchanged.
- [x] 1.2 Implement `ReadWriteOncePod` claim translation and validation for the pinned upstream, and run the admission test suite.
- [x] 1.3 Verify the selected upstream revision's PVC shape and rerun API-server tests for initial and repeated server-side apply, immutable fields, field ownership, and rejection of incompatible old claims.
- [ ] 1.4 Pin the selected released upstream revision and matching immutable images; verify release provenance and chart rendering, record the skipped isolated migration test and live migration result, and verify image interoperability.
- [x] 1.5 Build and validate the credential-free amd64 homelab project image from the released Claude project base; add pull-request validation and main-only GHCR publication, then resolve and pin its digest before assigning it to a project.

## 2. Durable workspace ownership

- [x] 2.1 Design and implement durable per-workspace identity and atomic reservation transitions before gate release; test competing candidates, stale UID/resourceVersion, lost responses, and process restart.
- [x] 2.2 Integrate reservations with pod gate release and side-effect-free checks for both binding APIs; test direct/pre-bound and legacy-binding bypasses, delayed bindings, dry runs, unauthorized updates, and API/webhook outages.
- [x] 2.3 Implement and test normal plain-Pod handoff: Kubernetes must report the previous pod gone before the next run uses its existing task PVC; missing, inconsistent, or unavailable API state blocks progress.
- [x] 2.4 Document the stable-worker assumption, out-of-scope failure modes, upstream retention/reset authorization, permissions, and retained-state procedures; review them against restart and API-failure tests.
- [x] 2.5 Adapt and test admission for upstream retention and reset of compatible managed NFS claims while preserving incompatible claims; exercise API errors and document the accepted name-only deletion race against replacement claims.
- [x] 2.6 Verify cleanup cannot follow sandbox-controlled symlinks, escape the workspace directory, delete another workspace or the export root, or automatically remove incompatible existing volumes.
- [x] 2.7 Adapt sandbox monitoring and RBAC to upstream's plain Pods rather than the removed Sandbox CRD; test wake, handoff, and power-sleep behavior.

## 3. Storage and filesystem semantics

- [x] 3.1 Onboard the NFS CSI driver and a dedicated StorageClass/export for new workspaces; constrain controller/worker access, render manifests, and verify existing volumes are unchanged.
- [x] 3.2 Implement and enforce the non-root `fsGroup` approach with `fsGroupChangePolicy: OnRootMismatch`; test that admission cannot silently weaken it. Do not implement a separate fail-closed root-mismatch preflight; document the accepted recursive-change risk.
- [x] 3.3 Using newly identified test claims with a root satisfying kubelet's check, verify exact `0755`, `0644`, and restrictive modes after repeated same-node and cross-node remounts; also test workspace reuse, Git executable bits, symlinks, and same-VM `flock`/record locks.
- [ ] 3.4 Test same-node and cross-node competing pods against one `ReadWriteOncePod` claim, concurrent pods using separate claims, and upstream retention/reset deletion; retain test resources unless their cleanup is explicitly approved.

## 4. Integration and staged operation

- [x] 4.1 Run the complete proxy/unit suite and Kustomize builds; verify all admission, RBAC, CSI and RuntimeClass resources render as intended.
- [ ] 4.2 In a controlled test, exercise normal handoff, delayed binding, controller restart, missing state, API outages, and retention/reset; verify task workspace reuse after normal handoff and record the accepted delayed-delete risk.
- [x] 4.3 Update the onboarding runbook, domain glossary, and architecture decision with NFS provisioning, the accepted root-mismatch mode-change risk, task-workspace retention and data-loss implications, existing-volume boundaries, GitOps sync order, and recovery/rollback instructions; keep real agents and sleep disabled until every acceptance gate passes.
- [ ] 4.4 After image provenance, credential isolation, power interlock, and workspace gates pass, run the documented fake-agent test and one human-reviewed documentation-only task; record results without exposing credentials.
- [x] 4.5 Raise the retained task-PVC quota to ten in chart values and the live namespace, preserving the two-pod and 200Gi limits; verify rendered values and live quota.
- [x] 4.6 Perform the user-authorized deletion of unused `task-home-2` and `task-home-3` claim generations through normal admission, preserve interlock records and newer workspaces, and verify PVC/PV cleanup and HOME-4 provisioning.
- [x] 4.7 Persist the mixed-NIC device string, Kata host-only socket load balancing, and config-change pod rollouts in a Cilium values file consumed by Ansible install/upgrade; locally verify chart rendering, checksum behavior, and playbook syntax without a live rollout.
