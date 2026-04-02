# Agentgateway Proxy Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision an agentgateway proxy pod (data plane), assign it a dedicated LAN IP (192.168.2.253), expose its Admin UI at `agent-gateway.home.timosur.com`, and route LLM inference traffic for Ollama, OpenAI, and Anthropic through the proxy at `192.168.2.253:8080`.

**Architecture:** The agentgateway controller (already in `agentgateway-system`) watches for `Gateway` resources with `gatewayClassName: agentgateway` and spawns a dedicated proxy Deployment + Service. Three traffic paths exist: (1) LLM inference via native agentgateway HTTPRoutes attaching directly to the proxy — clients hit `http://192.168.2.253:8080/ollama`, `/openai`, `/anthropic`; (2) Admin UI on port 15000 routed through the home Envoy Gateway; (3) Future MCP/A2A tool traffic also on port 8080. `AgentgatewayParameters` pins the proxy to `homelab-amd-desktop`. Ollama is already available in-cluster via ExternalName service. OpenAI and Anthropic keys come from Azure Key Vault via ExternalSecrets.

**Tech Stack:** Kubernetes Gateway API, agentgateway CRDs (`agentgateway.dev/v1alpha1`), Cilium LB IPAM (`CiliumLoadBalancerIPPool`), External Secrets Operator (`ClusterSecretStore: azure-keyvault-store`), Kustomize, Envoy Gateway (home gateway for Admin UI only), GitOps via ArgoCD.

---

## Architecture Diagram

```
MCP/A2A clients (OpenWebUI, kagent, external agents)
       │
       ▼
192.168.2.253:8080  ←── agentgateway proxy LoadBalancer (Cilium announced)
       │                 MCP/A2A tool traffic (port 8080)
       ▼
    agentgateway proxy pod
       │
       ├── port 8080 → MCP/A2A listener (Gateway spec)
       └── port 15000 → Admin UI (read-only in K8s mode)

Admin browser
       │
       ▼
agent-gateway.home.timosur.com → Envoy home gateway (192.168.2.100)
       │                          HTTPRoute → proxy svc port 15000
       ▼
    agentgateway proxy pod port 15000
```

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `apps/agentgateway/gateway.yaml` | `AgentgatewayParameters` (node scheduling) + `Gateway` (proxy provisioning) |
| Create | `apps/agentgateway/ai-secrets.yaml` | ExternalSecrets for OpenAI + Anthropic API keys from Azure Key Vault |
| Create | `apps/agentgateway/ai-backends.yaml` | `AgentgatewayBackend` CRDs for Ollama, OpenAI, Anthropic + `HTTPRoute` attaching to the proxy |
| Modify | `apps/agentgateway/kustomization.yaml` | Register all new manifests |
| Modify | `networking/cilium-lb-ipam/ip-pools.yaml` | Add IP pool for 192.168.2.253 → agentgateway proxy service |
| Create | `networking/httproutes/home/agent-gateway.yaml` | Expose Admin UI at `agent-gateway.home.timosur.com` → proxy port 15000 |
| Modify | `networking/httproutes/home/kustomization.yaml` | Register `agent-gateway.yaml` |

---

## Background Knowledge

- The agentgateway controller is already running in `agentgateway-system`. A `GatewayClass` named `agentgateway` (controller: `agentgateway.dev/agentgateway`) exists and is Accepted.
- No `Gateway` resource exists yet — the proxy pod is only created when one does.
- `homelab-amd-desktop` has taint `availability=daytime:NoSchedule`. All workloads targeting it need a matching toleration.
- `AgentgatewayParameters.spec.deployment.spec` accepts a partial PodSpec merged via Strategic Merge Patch — this is how nodeSelector + tolerations are injected into the proxy pod.
- **Cilium LB IPAM pattern** (existing in `networking/cilium-lb-ipam/ip-pools.yaml`): A `CiliumLoadBalancerIPPool` with a `serviceSelector` label match assigns a static IP to a LoadBalancer service. Cilium then BGP-announces it. The proxy service label used for selection must be confirmed after first deploy — likely `gateway.networking.k8s.io/gateway-name: agentgateway-proxy` (standard Gateway API label set by controllers on generated services).
- **HTTPRoute namespace rule**: The HTTPRoute must live in the same namespace as its backend service to avoid needing a ReferenceGrant. The proxy service will be in `agentgateway-system`, so the HTTPRoute also goes in `agentgateway-system`.
- The home Envoy Gateway (`envoy-gateway-home` in `envoy-gateway-system`) is already configured to accept HTTPRoutes from any namespace.
- When the agentgateway controller creates the proxy from a Gateway named `agentgateway-proxy`, it creates a Service also named `agentgateway-proxy` in `agentgateway-system`.
- Port 8080 = MCP/A2A listener. Port 15000 = Admin UI (read-only).

---

### Task 1: Create AgentgatewayParameters + Gateway manifest

**Files:**
- Create: `apps/agentgateway/gateway.yaml`

- [ ] **Step 1: Write `apps/agentgateway/gateway.yaml`**

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayParameters
metadata:
  name: agentgateway-proxy-params
  namespace: agentgateway-system
spec:
  deployment:
    spec:
      template:
        spec:
          nodeSelector:
            kubernetes.io/hostname: homelab-amd-desktop
          tolerations:
            - key: availability
              operator: Equal
              value: daytime
              effect: NoSchedule
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: agentgateway-system
spec:
  gatewayClassName: agentgateway
  infrastructure:
    parametersRef:
      group: agentgateway.dev
      kind: AgentgatewayParameters
      name: agentgateway-proxy-params
  listeners:
    - name: http
      protocol: HTTP
      port: 8080
      allowedRoutes:
        namespaces:
          from: All
```

- [ ] **Step 2: Commit**

```bash
git add apps/agentgateway/gateway.yaml
git commit -m "feat(agentgateway): add proxy Gateway and AgentgatewayParameters"
```

---

### Task 2: Register gateway.yaml in kustomization

**Files:**
- Modify: `apps/agentgateway/kustomization.yaml`

- [ ] **Step 1: Update `apps/agentgateway/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - gateway.yaml
  - ollama-backend.yaml
```

- [ ] **Step 2: Commit**

```bash
git add apps/agentgateway/kustomization.yaml
git commit -m "feat(agentgateway): register gateway manifest in kustomization"
```

---

### Task 3: Add Cilium IP pool for agentgateway proxy (192.168.2.253)

**Files:**
- Modify: `networking/cilium-lb-ipam/ip-pools.yaml`

The agentgateway controller creates a proxy Service with standard Gateway API labels. The label `gateway.networking.k8s.io/gateway-name: agentgateway-proxy` is the most likely selector — confirm after first deploy (see Task 5 Step 2). Add a new pool alongside the existing two.

- [ ] **Step 1: Append new pool to `networking/cilium-lb-ipam/ip-pools.yaml`**

```yaml
# Cilium LB IPAM pools for the home cluster
# Assigns dedicated IPs to each gateway's LoadBalancer service
#
# NOTE: Ensure k3s ServiceLB is disabled (--disable=servicelb) for clean
# Cilium LB IPAM operation. Update your k3s server flags and restart k3s.
---
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata:
  name: internet-gateway-pool
spec:
  blocks:
    - cidr: 192.168.2.254/32
  serviceSelector:
    matchLabels:
      gateway.envoyproxy.io/owning-gateway-name: envoy-gateway-internet
---
# TODO: This one I should actually switch to another IP, which is not the node IP
# because it can cause conflicts when the node is down and it should be different one.
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata:
  name: intranet-gateway-pool
spec:
  blocks:
    - cidr: 192.168.2.100/32
  serviceSelector:
    matchLabels:
      gateway.envoyproxy.io/owning-gateway-name: envoy-gateway-home
---
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata:
  name: agentgateway-proxy-pool
spec:
  blocks:
    - cidr: 192.168.2.253/32
  serviceSelector:
    matchLabels:
      gateway.networking.k8s.io/gateway-name: agentgateway-proxy
```

- [ ] **Step 2: Commit**

```bash
git add networking/cilium-lb-ipam/ip-pools.yaml
git commit -m "feat(networking): add Cilium IP pool for agentgateway proxy at 192.168.2.253"
```

---

### Task 4: Create HTTPRoute for Admin UI

**Files:**
- Create: `networking/httproutes/home/agent-gateway.yaml`

- [ ] **Step 1: Write `networking/httproutes/home/agent-gateway.yaml`**

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: agent-gateway
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: envoy-gateway-home
      namespace: envoy-gateway-system
  hostnames:
    - agent-gateway.home.timosur.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: agentgateway-proxy
          namespace: agentgateway-system
          port: 15000
```

- [ ] **Step 2: Commit**

```bash
git add networking/httproutes/home/agent-gateway.yaml
git commit -m "feat(networking): expose agentgateway Admin UI at agent-gateway.home.timosur.com"
```

---

### Task 5: Register HTTPRoute in kustomization

**Files:**
- Modify: `networking/httproutes/home/kustomization.yaml`

- [ ] **Step 1: Update `networking/httproutes/home/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - agent-gateway.yaml
  - agents.yaml
  - agents-backend-policy.yaml
  - argocd.yaml
  - garden.yaml
  - grafana.yaml
  - home-assistant.yaml
  - pi-hole.yaml
  - vinyl-manager.yaml
```

- [ ] **Step 2: Commit**

```bash
git add networking/httproutes/home/kustomization.yaml
git commit -m "feat(networking): register agent-gateway HTTPRoute in kustomization"
```

---

### Task 6: Push and verify

- [ ] **Step 1: Push all commits**

```bash
git push
```

Wait ~60s for ArgoCD to sync.

- [ ] **Step 2: Confirm proxy Service labels (required to validate IP pool selector)**

```bash
kubectl get svc -n agentgateway-system -o wide
kubectl get svc agentgateway-proxy -n agentgateway-system -o jsonpath='{.metadata.labels}' | python3 -m json.tool
```

Expected: Service exists. Confirm it has label `gateway.networking.k8s.io/gateway-name: agentgateway-proxy`.
If the label key differs, update `networking/cilium-lb-ipam/ip-pools.yaml` `serviceSelector` to match and push again.

- [ ] **Step 3: Confirm proxy Service got 192.168.2.253**

```bash
kubectl get svc agentgateway-proxy -n agentgateway-system
```

Expected: `EXTERNAL-IP` = `192.168.2.253`.

- [ ] **Step 4: Verify proxy pod is on homelab-amd-desktop**

```bash
kubectl get pods -n agentgateway-system -o wide
```

Expected: `agentgateway-proxy-<hash>` is `Running` on `homelab-amd-desktop`.

- [ ] **Step 5: Verify port 15000 (Admin UI)**

```bash
kubectl port-forward -n agentgateway-system svc/agentgateway-proxy 15000:15000 &
curl -s -o /dev/null -w "%{http_code}" http://localhost:15000/
kill %1
```

Expected: HTTP `200`.

- [ ] **Step 6: Verify Admin UI via HTTPRoute**

Browse to `http://agent-gateway.home.timosur.com` — should show the agentgateway Admin UI.

- [ ] **Step 7: Verify MCP port 8080 reachable at 192.168.2.253**

```bash
curl -s -o /dev/null -w "%{http_code}" http://192.168.2.253:8080/
```

Expected: HTTP `200` or `404` (any response means the proxy is reachable).

---

---

### Task 7: Create ExternalSecrets for API keys

**Files:**
- Create: `apps/agentgateway/ai-secrets.yaml`

The `AgentgatewayBackend` `auth.secretRef` reads the `Authorization` key from a K8s Secret and passes it as the `Authorization` HTTP header to the upstream provider. For OpenAI the value must be `Bearer sk-...`. For Anthropic the provider internally maps it to the `x-api-key` header, so the value is just the raw API key.

Add the following keys to Azure Key Vault before deploying:
- `agentgateway-openai-api-key` → value: raw OpenAI API key, e.g. `sk-proj-...` (**no** `Bearer` prefix — agentgateway adds it)
- `agentgateway-anthropic-api-key` → value: raw Anthropic API key, e.g. `sk-ant-...` (**no** prefix — agentgateway sends it as `x-api-key` header)

- [ ] **Step 1: Add API keys to Azure Key Vault**

In the Azure Portal or CLI, create two secrets in the Key Vault used by `azure-keyvault-store`:
```bash
az keyvault secret set --vault-name <your-vault> --name agentgateway-openai-api-key --value "Bearer sk-proj-..."
az keyvault secret set --vault-name <your-vault> --name agentgateway-anthropic-api-key --value "sk-ant-..."
```

- [ ] **Step 2: Write `apps/agentgateway/ai-secrets.yaml`**

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: agentgateway-openai-secret
  namespace: agentgateway-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: agentgateway-openai-secret
    creationPolicy: Owner
  data:
    - secretKey: Authorization
      remoteRef:
        key: agentgateway-openai-api-key
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: agentgateway-anthropic-secret
  namespace: agentgateway-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: agentgateway-anthropic-secret
    creationPolicy: Owner
  data:
    - secretKey: Authorization
      remoteRef:
        key: agentgateway-anthropic-api-key
```

- [ ] **Step 3: Commit**

```bash
git add apps/agentgateway/ai-secrets.yaml
git commit -m "feat(agentgateway): add ExternalSecrets for OpenAI and Anthropic API keys"
```

---

### Task 8: Create AI backends and inference HTTPRoute

**Files:**
- Create: `apps/agentgateway/ai-backends.yaml`

This creates three `AgentgatewayBackend` CRDs and one `HTTPRoute` that attaches to the `agentgateway-proxy` Gateway (NOT to Envoy). Clients reach LLM inference at `http://192.168.2.253:8080/{ollama,openai,anthropic}/v1/chat/completions`.

Ollama uses the `openai` provider type (Ollama exposes an OpenAI-compatible API) with `host`/`port`/`path` overrides pointing to the existing ExternalName service in the same namespace. No auth needed for Ollama.

- [ ] **Step 1: Write `apps/agentgateway/ai-backends.yaml`**

**One HTTPRoute with multiple path-prefix rules** — this is the official multi-provider pattern from the agentgateway docs. Clients select a provider by path prefix; agentgateway strips the prefix and routes to the correct backend, handling endpoint rewriting per provider (e.g. Anthropic auto-rewrites to `/v1/messages`).

Client URLs:
- `http://192.168.2.253:8080/ollama/v1/chat/completions`
- `http://192.168.2.253:8080/openai/v1/chat/completions`
- `http://192.168.2.253:8080/anthropic/v1/messages`

```yaml
# Ollama — OpenAI-compatible API, no auth, in-cluster ExternalName service
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: ollama
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: devstral:latest
      host: ollama.agentgateway-system.svc.cluster.local
      port: 11434
---
# OpenAI — raw key in secret (no Bearer prefix, framework adds it)
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: openai
  namespace: agentgateway-system
spec:
  ai:
    provider:
      openai:
        model: gpt-4o-mini
  policies:
    auth:
      secretRef:
        name: agentgateway-openai-secret
---
# Anthropic — raw key in secret, framework sends as x-api-key automatically
# agentgateway auto-rewrites endpoint to /v1/messages
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: anthropic
  namespace: agentgateway-system
spec:
  ai:
    provider:
      anthropic:
        model: claude-sonnet-4-6
  policies:
    auth:
      secretRef:
        name: agentgateway-anthropic-secret
---
# Single HTTPRoute with path-based rules — attaches to agentgateway-proxy (NOT Envoy)
# Pattern confirmed by official agentgateway multi-provider README example
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-inference
  namespace: agentgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: agentgateway-system
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /ollama
      backendRefs:
        - name: ollama
          namespace: agentgateway-system
          group: agentgateway.dev
          kind: AgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      backendRefs:
        - name: openai
          namespace: agentgateway-system
          group: agentgateway.dev
          kind: AgentgatewayBackend
    - matches:
        - path:
            type: PathPrefix
            value: /anthropic
      backendRefs:
        - name: anthropic
          namespace: agentgateway-system
          group: agentgateway.dev
          kind: AgentgatewayBackend
```

- [ ] **Step 2: Commit**

```bash
git add apps/agentgateway/ai-backends.yaml
git commit -m "feat(agentgateway): add Ollama, OpenAI, Anthropic AI backends and inference HTTPRoute"
```

---

### Task 9: Register new manifests in kustomization

**Files:**
- Modify: `apps/agentgateway/kustomization.yaml`

- [ ] **Step 1: Update `apps/agentgateway/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - ai-backends.yaml
  - ai-secrets.yaml
  - gateway.yaml
  - ollama-backend.yaml
```

- [ ] **Step 2: Commit**

```bash
git add apps/agentgateway/kustomization.yaml
git commit -m "feat(agentgateway): register AI backend manifests in kustomization"
```

---

### Task 10: Verify LLM inference after ArgoCD sync

- [ ] **Step 1: Push and wait for ArgoCD sync**

```bash
git push
```

Wait ~60s.

- [ ] **Step 2: Check backends and HTTPRoute are accepted**

```bash
kubectl get agentgatewaybackend -n agentgateway-system
kubectl get httproute llm-inference -n agentgateway-system
```

Expected: all backends `Ready`, HTTPRoute `Accepted`.

- [ ] **Step 3: Verify ExternalSecrets synced**

```bash
kubectl get secret agentgateway-openai-secret agentgateway-anthropic-secret -n agentgateway-system
```

Expected: both secrets exist with an `Authorization` key.

- [ ] **Step 4: Test Ollama inference (no auth)**

```bash
curl -s http://192.168.2.253:8080/ollama/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"devstral:latest","messages":[{"role":"user","content":"Say hi"}],"stream":false}' \
  | python3 -m json.tool
```

Expected: JSON response with `choices[0].message.content`.

- [ ] **Step 5: Test OpenAI inference**

```bash
curl -s http://192.168.2.253:8080/openai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Say hi"}],"stream":false}' \
  | python3 -m json.tool
```

Expected: JSON response from OpenAI.

- [ ] **Step 6: Test Anthropic inference**

agentgateway translates `/v1/chat/completions` (OpenAI format) to Anthropic's `/v1/messages` internally — clients always use the OpenAI-compatible format.

```bash
curl -s http://192.168.2.253:8080/anthropic/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"Say hi"}],"stream":false}' \
  | python3 -m json.tool
```

Expected: JSON response from Anthropic, returned in OpenAI-compatible format.

---

## Known Risks

- **Service label selector**: The Cilium pool selector uses `gateway.networking.k8s.io/gateway-name: agentgateway-proxy`. If the controller uses a different label, the pool won't assign the IP. Task 6 Step 2 catches this.
- **Port 15000 not in generated Service**: The controller-created Service may only expose port 8080 and not 15000 (Admin UI). If `kubectl get svc agentgateway-proxy` doesn't show port 15000, the HTTPRoute in Task 4 will fail. Check by port-forwarding directly to the pod (`kubectl port-forward pod/<proxy-pod> 15000:15000`) to confirm the port is open, then create a separate Service manually exposing 15000.
- **Ollama `host`/`port` are siblings of `openai`**: Confirmed by official docs — `host` and `port` sit at `spec.ai.provider` level alongside `openai`, not nested inside it. If the controller rejects this, check `kubectl describe agentgatewaybackend ollama` for validation errors.
- **Anthropic `x-api-key` header**: Confirmed by official docs — store the raw API key under the `Authorization` secret key; agentgateway automatically sends it as `x-api-key`. Do not add any prefix.
- **OpenAI secret no `Bearer` prefix**: Confirmed by official docs — store the raw `sk-...` key; the framework adds the `Authorization: Bearer` prefix. Adding `Bearer` yourself will double it and cause 401s.
- **Path stripping**: The official examples show path-prefix matching (`/openai`, `/anthropic`) but don't explicitly show whether agentgateway strips the prefix before forwarding. If Ollama receives `/ollama/v1/chat/completions` instead of `/v1/chat/completions` it will 404. Check the Admin UI after deploy to confirm, and add a rewrite filter if needed.
