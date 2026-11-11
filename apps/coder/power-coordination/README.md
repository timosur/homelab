# Staged Coder power coordination

The ArgoCD application initially deploys only the prerequisites. Do not publish the workspace
template until the reviewed `wol-proxy` image, preflight configuration, and fail-closed admission
are deployed and verified in the cluster.

Before enabling:

1. Review and deploy the pinned `wol-proxy` image, then verify its rollout and existing power
   safeguards before enabling the Coder integration. Create a random, URL-safe token of at least
   32 bytes in Key Vault as `coder-power-preflight-token`; do not record its value in Git or logs.
2. Sync `apps/_argocd/coder-power-app.yaml` and verify both `coder-power-preflight` ExternalSecrets
   are ready. The application excludes the live `data` of `coder/coder-power-trust` from
   reconciliation: `wol-proxy` writes its public admission CA there and keeps it current after
   rotation. Start with no ungated Coder workspaces.
3. Enable both Coder settings in `apps/wol-proxy/configmap.yaml` and restart the proxy so it reads
   them. Confirm the CA is populated and the Coder admission and preflight endpoints
   are available. While the webhooks are missing, the proxy fails closed: it cannot suspend the pool
   or coordinate other protected workload starts. Perform steps 3 and 4 in a maintenance window.
4. Add `admission-webhooks.yaml` to this Kustomization and sync it. Confirm both webhook
   configurations have their CA and fail-closed policy before allowing workspace starts. An
   unavailable webhook can block Coder namespace pod creation and scheduling, including replacement
   of the control-plane pod; plan activation and recovery as a maintenance window.
5. Publish the workspace template and run the change's live power and workspace acceptance tests.

An interrupted start may leave a durable workspace reservation in the pool interlock. Do not
remove it until an operator has verified the corresponding build and any in-flight pod creation
are resolved; the separate Coder scheduling gate must remain active during recovery.
