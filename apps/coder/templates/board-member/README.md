# Board-member Coder workspace template

> **Not deployed yet:** these instructions describe the intended workflow after the control plane
> and power coordination have been deployed and verified.

This template creates one independently owned development workspace and 100 GiB Synology iSCSI
home volume for each Coder workspace. The volume survives a workspace stop, while the Pod is
created only while the workspace is running, on either approved worker in the shared power pool.

## Administrator: publish the template

Do not publish this template until the staged power coordination in
[`../../power-coordination/README.md`](../../power-coordination/README.md) is deployed. Its
authenticated preflight must succeed before workspace storage or its Pod can be created.

## Administrator: workspace isolation rollout

The workspace egress policy applies to **existing** labeled pods as soon as Cilium reconciles it.
Coordinate a maintenance window, stop the Coder workspaces normally, and keep users from starting
them until the new policy, agent gateway and template are ready. Do not delete their home PVCs or
bypass the existing workspace admission and power interlocks. Deprecating a Coder template alone
does not prevent existing workspaces from starting; coordinate starts explicitly.

1. From `ansible/`, run `ansible-playbook playbooks/coder-workspace-seccomp.yml --check --diff`,
   then run it without `--check --diff`. Verify both eligible workers have the same root-owned
   `/var/lib/kubelet/seccomp/coder-workspace.json` before publishing the template. Kubernetes will
   refuse to start the workspace container if the selected node lacks that profile.
2. Reconcile the networking and Tetragon applications. Verify the Coder-only Gateway is Programmed,
   its HTTPRoute is Accepted/ResolvedRefs, `coder-agent-gateway` is a ClusterIP service, and
   `coder-workspace-egress` and `coder-workspace-setns` exist. On each worker verify that its
   Tetragon agent is Ready and `tetra tracingpolicy list` reports the policy enabled; also inspect
   the Cilium endpoint's realized egress policy after a workspace starts. Networking auto-sync is
   currently disabled on the live cluster; use a manual ArgoCD sync without changing that setting.
3. Publish the template and test a workspace on each worker before relying on isolation. Verify DNS,
   public HTTPS, private Git over Coder authentication, `sudo`/APT, Zed/SSH and agent connectivity;
   verify LAN, node, Kubernetes API and unrelated cluster services are blocked. Test a controlled
   namespace-join attempt, inspect its attributed Tetragon event, and confirm ordinary container
   startup and `kubectl exec` still work. Confirm the shared home gateway cannot be used with a
   different SNI or HTTP Host. If any result or either worker's coverage is inconclusive, stop the
   protected rollout and retain the workspace PVCs.

The agent still uses `coder.home.timosur.com` with its existing TLS certificate. The template reads
the Coder-only gateway Service IP at each start and maps that name to the isolated gateway inside
the workspace. The gateway has only the Coder route; it is not the shared home gateway. If its
Service is recreated with a different IP, restart the workspace after confirming the new gateway is
ready. The Service read is start-only, so a missing gateway must not prevent a workspace stop.

The seccomp profile is based on the workers' containerd 2.2.3 `RuntimeDefault`, with `setns`
allowed so Tetragon can see the attempt. Compare it against a disposable pod's realized
`RuntimeDefault` profile on **both** workers after any containerd upgrade; review differences
before publishing an updated workspace template. Only inspect the `linux.seccomp` portion of a
disposable pod's runtime specification, not an actual workspace's environment or credentials.
The pod drops `SYS_ADMIN` and other host-management capabilities; this independent privilege check
still matters if Tetragon is unavailable. Tetragon's signal does not guarantee every syscall had no
effect. A shared-kernel exploit, a compromised allowed DNS/Coder service, or a public relay can
bypass parts of this defense; this is not VM isolation.

The changing home public IP is intentionally allowed as internet traffic. A workspace can reach
the UniFi management interface through that address if the router exposes it; this exposure was
explicitly accepted and is not prevented by the private-LAN deny policy.

If the policy or profile breaks a workspace, stop it normally and coordinate a GitOps rollback of
the template, policy and gateway together. Confirm the restored template and route before allowing
starts; retain the PVCs and do not bypass the power guards. A rolled-back workspace is **not**
protected by this isolation. Avoid collecting tokens, credentials or private repository contents
in acceptance evidence or Tetragon logs.

Validate and publish from this directory after logging in to the private Coder instance:

```sh
terraform init
terraform validate
./publish.sh
```

The template pins the Coder workspace image by digest. It requires the Coder `github` external-auth
provider before a workspace can be created; it does not mount a personal GitHub credential.

`publish.sh` sets Coder's default autostop and activity bump to 30 minutes. Each detected workspace
session activity extends its deadline by 30 minutes; without activity, Coder stops the workspace
after 30 minutes while its home PVC remains.

## Board member: create and connect

From the home network, open [Coder](https://coder.home.timosur.com/) and sign in through Authentik.
Create a workspace from the `board-member` template. When prompted, authorize GitHub; authorization
is required before the workspace can start. Once the service is available, the CLI can also create a
workspace (replace `my-workspace` with your own name):

```sh
coder login https://coder.home.timosur.com
coder create --template=board-member my-workspace
coder config-ssh
zed ssh://coder.my-workspace/home/coder
```

Follow the GitHub authorization URL if the CLI prompts for it, then retry. `coder config-ssh` asks
before changing your local SSH configuration; use `coder config-ssh --dry-run` to inspect the change
first. Zed connects through the Coder SSH configuration, not by SSH to a worker host. You can open a
project below `/home/coder` instead of the home directory.

Use HTTPS Git URLs such as `https://github.com/<owner>/<repository>.git`. Coder supplies GitHub
authentication for approved repositories; do not save a personal GitHub token or SSH private key in
the workspace. Dotfiles are installed non-interactively from the configured repository's default
branch. If bootstrap or a private clone fails, check the Coder workspace build and agent logs without
sharing credentials.

## Stop, start, and persistence

```sh
coder stop my-workspace
coder start my-workspace
coder list
```

Starting may take time while the shared worker pool wakes and becomes ready. A failed preflight
should be retried after the cause is resolved; contact the administrator if it remains blocked.
Stopping removes the workspace Pod but retains its 100 GiB home volume. Files in `/home/coder`,
including cloned repositories and dotfiles, persist across stops and rebuilds. Packages installed
with APT and changes outside that home volume are **not durable** across a rebuild; add shared
tools to the workspace image instead. Do not delete the workspace or its PVC as a substitute for
stopping it: deletion is a separate, potentially destructive operation.

The published template defaults to stopping after 30 minutes without detected workspace activity.
Coder tracks sessions such as SSH, terminal, and supported IDE connections; an open session can
keep the workspace active. Close Zed and SSH sessions when finished. Opening the dashboard or
accessing a forwarded port alone does not count as a session. After a stop, the pool becomes eligible
for its existing suspension checks only when no other protected workload requires it.

## Administrator: acceptance sequence

Run only after the control plane, the reviewed power integration, and the template are deployed.
Coordinate with other users before any power-state test; do not force suspension, bypass admission,
or delete a PVC to clear a failed test. Record pass/fail and non-sensitive evidence in the change
handoff; never record tokens, passwords, or private repository contents.

1. Verify the home-only HTTPS route, Authentik login, database claim and ExternalSecrets. Confirm
   no public Pangolin/Newt route exposes Coder.
2. With the pool safely asleep under its existing policy, start a board-member workspace. Confirm
   both workers become Ready and schedulable before its Pod runs, and that it lands on an approved
   worker with a separate 100 GiB `homelab-iscsi` PVC. Test a denied preflight and a readiness
   timeout; neither may create or delete workspace storage, and a retry must be possible.
3. Complete required GitHub authorization, clone an approved private repository over HTTPS, and
   verify non-interactive dotfiles bootstrap. Use `coder login`, `coder config-ssh`, and Zed to open
   the running workspace through Coder.
4. Create a non-sensitive marker under `/home/coder`, stop and restart the workspace, and verify
   the marker remains. Confirm a second member's workspace has a different PVC.
5. Close all workspace sessions and observe the 30-minute inactivity stop. Confirm the Pod is
   removed but its PVC remains. With another protected workload present, confirm the pool stays
   awake; after it stops, confirm only the existing power-safety policy can suspend the pool. At the
   next start, verify the marker still exists.
6. If a start or gate handoff is uncertain, preserve storage and follow the reservation recovery
   guidance in [`../../power-coordination/README.md`](../../power-coordination/README.md).
