# Staged Coder power coordination

This Kustomization is deliberately not connected to ArgoCD: the currently pinned `wol-proxy`
image does not serve the Coder admission and preflight endpoints. Do not publish the workspace
template until the new image, preflight configuration, and fail-closed admission are deployed.

Before enabling:

1. Publish and pin the reviewed `wol-proxy` image. Create a random, URL-safe token of at least
   32 bytes in Key Vault as `coder-power-preflight-token`; do not record its value in Git or logs.
2. Enable the staged `apps/_argocd/coder-power-app.yaml` through the ArgoCD parent. It excludes the
   live `data` of `coder/coder-power-trust` from reconciliation: `wol-proxy` writes its public
   admission CA there and keeps it current after rotation. Start with no ungated Coder workspaces.
3. Enable both commented Coder settings in `apps/wol-proxy/configmap.yaml` and restart the proxy
   so it reads them. Confirm the CA, both ExternalSecrets, and both fail-closed webhook
   configurations are ready before allowing workspace starts. Missing or inconsistent admission
   prevents pool suspension.
4. Publish the workspace template and run the change's live power and workspace acceptance tests.

An interrupted start may leave a durable workspace reservation in the pool interlock. Do not
remove it until an operator has verified the corresponding build and any in-flight pod creation
are resolved; the separate Coder scheduling gate must remain active during recovery.
