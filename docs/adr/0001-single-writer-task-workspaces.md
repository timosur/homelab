# Single-writer task workspaces

Dark Factory task workspaces use single-pod ownership of per-task directories on a dedicated
Synology NFS export rather than concurrent sharing over SMB or one block volume per task. Kata's
bundled virtiofsd does not forward advisory locks between VMs, the existing SMB mount does not
preserve the file modes required by Git, and per-task block volumes have an operational scale
limit. Different tasks can still run concurrently on either sandbox worker.

Sequential runs reuse the task workspace. Upstream retention removes eligible workspaces after seven
days for completed or cancelled tasks, or 30 days idle for any task; an authorized reset can remove
them sooner. Deletion loses unpushed changes and local session state.

The system assumes stable workers and normal Kubernetes teardown. A surviving writer after node
failure or force deletion is outside this guarantee. Upstream's delayed name-only delete can also
delete a replacement PVC and its data; this risk is accepted for now. Separate power-sleep
safeguards are unchanged. Existing incompatible workspaces are preserved for explicit migration.
Exact file modes must survive normal workspace remounts when the root satisfies kubelet's
`OnRootMismatch` check; this remains an acceptance condition. Admission must enforce that policy.
The user accepts that a root mismatch or failed root inspection can trigger recursive ownership and
mode changes, including broader group access. No separate fail-closed root preflight is planned, so
exact modes are not guaranteed in that case.
