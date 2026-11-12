# Proposal

## Why

Coder development pods run user-controlled code and currently have no dedicated egress or runtime
escape controls. Restricting their reach and adding defense in depth reduces the consequences of a
compromised workspace without disrupting the personal development workflow.

## What Changes

- **BREAKING:** Restrict Coder workspace egress to public internet, cluster DNS, and the verified
  Coder connectivity needed for its agent; deny other LAN, node, Kubernetes API, and cluster
  destinations, including indirect access through shared internal gateways.
- Harden workspace pod isolation and add workspace-scoped Tetragon enforcement and visibility for
  selected escape-related operations, while retaining passwordless sudo and interactive APT.
- Verify protection and normal development on both eligible workers, including effective policy
  coverage, permitted and denied connections, and representative runtime enforcement.
- Document operational checks, recovery, and the shared-kernel and allowed-service limitations; do
  not claim that Cilium and Tetragon prevent every container escape.
- Permit the changing home public IP as part of public-internet access, even if a router
  management interface is reachable through it; the user explicitly accepts that exposure.
- Leave Dark Factory and non-workspace workloads unchanged.

## Capabilities

### New Capabilities
- `coder-workspace-isolation`: Network isolation and runtime defense in depth for personal Coder
  development workspace pods.

### Modified Capabilities
- None.

## Impact

This builds on the in-progress `onboard-coder-dev-workspace` change and its workspace labels,
template, agent connectivity, and lifecycle. It affects Cilium policy, the home gateway path if
needed to avoid an internal proxy bypass, the Coder workspace pod security context, Tetragon GitOps
policies, and workspace operator guidance. Existing Coder, DNS, GitHub, APT, Zed, and power
coordination behavior must continue to work. Tetragon is already installed on amd64 nodes; coverage
and actual kernel support on both eligible workers must be verified rather than assumed.
