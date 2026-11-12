# Design

## Context

See [proposal.md](proposal.md) and the [isolation spec](specs/coder-workspace-isolation/spec.md).
The in-progress Coder template creates standard shared-kernel pods in the same namespace as the
control plane, with two stable workspace labels, no mounted service-account token, and sudo. Its
agent uses the home HTTPS address; the gateway also routes other applications. Cilium's existing
cluster-wide ingress policy allows same-namespace ingress, but there is no workspace egress policy.
Tetragon 1.7.1 is installed through ArgoCD; its local policy directory is currently empty.

## Goals / Non-Goals

**Goals:** Apply workspace-only restrictions without changing the Coder server or Dark Factory
workloads, preserve the approved development flow, and make both-worker coverage verifiable.

**Non-Goals:** A VM isolation boundary, a proof against shared-kernel exploits, blocking public
internet or public relays, and a new general-purpose network segmentation system. This does not
change the personal workspace's retained storage or the existing power safety guarantees.

## Decisions

### Select workspace pods and isolate effective egress

Use the Coder namespace plus both existing workspace labels as the Cilium and Tetragon selectors;
do not select the Coder control plane. Apply Cilium egress isolation before workspace use, allowing
only cluster DNS, the actual Coder agent path, and external internet excluding local/private,
link-local, loopback and other non-public ranges appropriate to enabled IPv4 and IPv6 routes.
Account for Cilium's distinct identities for cluster endpoints, host, remote nodes, and the API
server rather than relying on CIDR exclusions alone. Check Service IPs, load-balancer IPs, direct
pod and node IPs, and DNS answers against the effective datapath. Explicit denies may be needed
where another policy's additive allow would otherwise bypass the baseline; test their precedence
and do not let an overly broad deny mask the required DNS and Coder exceptions.

Do not grant egress to the shared home gateway merely because it routes Coder: TLS SNI and HTTP Host
can select other applications. Prefer a supported agent connection directly to a narrowly scoped
Coder-only endpoint, with authentication and appropriate transport security. If that path is not
supported, use an equivalently isolated Coder-only gateway/route and verify that changing SNI or
Host cannot reach any other application. Confirm the actual Coder agent, DERP, DNS, TLS and Zed
behavior with the pinned versions before choosing the concrete route. Do not silently weaken the
boundary if neither approach works.

Use a dedicated ClusterIP Envoy Gateway with one HTTPS listener for `coder.home.timosur.com` and
only the Coder HTTPRoute. The provisioner has `get` access only to that named Service, and the
workspace template resolves its current ClusterIP on starts and adds a pod host alias for the
unchanged canonical hostname. This retains certificate verification and gives the agent and its
embedded relay hostname a Coder-only route. The Service read is start-only so an unavailable
gateway cannot prevent stopping a workspace. A Service IP replacement requires a workspace restart.
The gateway path, including real agent and DERP behavior, must pass acceptance before rollout.

**Alternatives considered:** Reusing the internet-namespace policy would grant all cluster
endpoints and the API and does not cover this namespace. Allowing the existing shared gateway is
not a Coder-only exception. A public FQDN allow-list would unnecessarily restrict the explicitly
approved public-internet workflow.

### Keep sudo with a constrained pod and scoped runtime enforcement

Keep the non-root user and sudo. Explicitly prohibit host networking/PID/IPC, host mounts,
privileged containers and token automounting. Set a seccomp profile and drop kernel capabilities,
restoring only the minimum demonstrated necessary for sudo and APT; never add host-administration
capabilities such as `SYS_ADMIN`, `SYS_MODULE`, `SYS_PTRACE`, `NET_ADMIN`, or `BPF` for this
workflow. Because sudo uses setuid, do not set `allowPrivilegeEscalation: false` without a tested
alternative that preserves the approved behavior. Verify that the selected runtime/profile and
image actually retain the expected restrictions after sudo.

Add GitOps-managed, label-and-namespace-scoped Tetragon tracing policies for tested escape-related
operations, including a namespace-join attempt, with in-kernel enforcement and attributable events.
Verify support and policy load on both tainted workers and choose hooks/actions supported by their
running kernels and Tetragon 1.7.1. Seccomp can reject a syscall before a Tetragon syscall hook runs;
validate the actual combined behavior instead of assuming every seccomp denial produces an event. If
the required runtime event needs a tailored seccomp profile, retain the independent capability
restriction and validate the resulting exposure; do not weaken the baseline silently.

A live disposable probe showed that a pod-label-only `setns` rule also matches containerd/runc
while it enters the pod, breaking `kubectl exec`. Restrict this hook to processes whose PID
namespace is not the host PID namespace; verify both runtime exec and in-container enforcement on
each worker. The current containerd `RuntimeDefault` rejects `setns` before the hook, so install a
node-local profile based on the workers' containerd 2.2.3 default with only `setns` added to its
allow-list. Recheck the baseline whenever containerd changes. Install the profile on both workers
before publishing the template; a missing profile must prevent the container from starting.
Allowing the syscall exposes more kernel surface than `RuntimeDefault`: dropping `SYS_ADMIN` and
the other host-management capabilities remains an independent control even when Tetragon is down.
Tetragon's `Sigkill` terminates the caller but is not a universal guarantee that a syscall had no
effect; do not describe it as a substitute for kernel privilege checks.

**Alternatives considered:** Removing sudo would break the approved APT workflow. Kata would offer
a different kernel boundary but is not part of this approved change. A monitoring-only policy
would not satisfy the requested runtime enforcement.

### Verify and operate rather than overstate the boundary

Render and test selectors, namespace scoping, address-family and policy combinations, including
other allowed policies. Exercise live enforcement with controlled, non-destructive probes on each
worker and a non-workspace negative control; check Tetragon policy load and event attribution, plus
network denials and required Coder workflows. A daemon can be absent, restart, or fail to load a
policy: do not infer continuous fail-closed runtime enforcement from a GitOps manifest. Document
how to detect those states, how to stop relying on the protection, and how to recover without
deleting PVCs. The protected rollout is not complete until both workers pass.

## Risks / Trade-offs

- [Root inside a shared kernel or a kernel exploit bypasses controls] → Minimize capabilities and
  use seccomp and Tetragon as defense in depth; explicitly document that escape prevention is not
  guaranteed, particularly while Tetragon is unavailable.
- [Allowed DNS, Coder or an external relay is used indirectly] → Scope the Coder path, treat DNS
  and Coder as trusted dependencies, and do not claim protection from compromised dependencies or
  arbitrary public relays.
- [The home WAN IP exposes UniFi management] → A live test reached the UniFi login through the
  public address. The WAN IP can change; after reviewing the options, the user explicitly chose to
  remove the temporary public-IP deny and accept this exposure rather than block internet access
  or add router-level enforcement. Router authentication remains the boundary for that address.
- [Cilium additive policies or translated traffic bypass the intended boundary] → Inspect effective
  policies and test representative pod, Service, host, gateway, API, IPv4 and enabled IPv6 paths.
- [A runtime rule breaks APT or developer tooling] → Test sudo, package installation and workspace
  bootstrap before rollout; scope enforcement to well-defined operations and provide rollback.
- [A worker is asleep or policy/agent support is missing] → Wake it through existing safe power
  controls and leave acceptance incomplete until both workers are covered.

## Migration Plan

1. Coordinate with `onboard-coder-dev-workspace`; stop existing Coder workspaces normally and
   coordinate a maintenance window so none start while policies and the template are changed.
   Install the profile on both workers, then deploy and verify the Coder-only agent path and
   workspace-scoped network and runtime controls before publishing the new template.
2. Validate on each worker with disposable probes and the existing end-to-end Coder workflow;
   retain existing admission, power interlocks and PVCs. Do not declare rollout complete on failure.
3. If compatibility or policy checks fail, stop affected workspaces and revert the new policy and
   template changes through GitOps as a coordinated rollback. Preserve home PVCs and explicitly
   report that rolled-back workspaces no longer have this protection.
