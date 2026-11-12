# Proposal

## Why

An obsolete ingress allowance still grants Coder workspace pods access to the shared home gateway.
Although workspace egress currently denies that route, the allowance weakens defense in depth.

## What Changes

- Remove the obsolete shared-gateway policy and its inclusion in networking manifests.
- Replace the outdated test with a regression check; retain the dedicated Coder gateway policy.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- None. This removes a redundant allow that is already overridden by the existing egress policy;
  the `coder-workspace-isolation` requirements remain unchanged.

## Impact

The networking Kustomization and Coder policy tests change. Existing workspace access continues
through the dedicated Coder gateway.
