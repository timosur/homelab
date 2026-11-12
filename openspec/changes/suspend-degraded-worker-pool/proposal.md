# Proposal

## Why

The shared sleep coordinator returns early whenever any worker is NotReady, leaving an idle, reachable GPU awake indefinitely after its pool peer becomes unavailable. NotReady does not establish whether the peer is asleep, failed, or still running.

## What Changes

- Allow safe idle suspension of reachable pool members even when another member is NotReady or unreachable.
- Preserve workload, admission, interlock, and host-fencing safeguards; keep workspace release blocked until the complete pool is ready and fenced.
- Handle partial and uncertain power operations without treating an unavailable node as confirmed asleep or allowing stale suspend requests to race later activity.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `coder-dev-workspaces`: Specify safe degraded-pool idle sleep while retaining the two-worker workspace start requirement.

## Impact

The `wol-proxy` shared pool coordinator, power-client orchestration, and their tests; operator guidance for degraded or uncertain host power state. Live behavior changes only after the updated proxy is built and deployed.
