# OpenClaw Multi-Agent Deployment Plan

## Overview

Deploy OpenClaw as a multi-agent-ready architecture in the `openclaw` namespace. Start with one agent ("research"), but design all infrastructure for N agents sharing a common file-based BRAIN. Each agent gets its own Deployment, PVC (private workspace), config, and secrets, but all mount a shared `ReadWriteMany` SMB volume as their BRAIN — a structured directory where agents read/write knowledge files (markdown, JSON). A per-agent naming convention (`<agent-role>-deployment.yaml`, etc.) keeps manifests clean. CiliumNetworkPolicy locks down each agent's egress to public internet only. Management plane is deferred — agents coordinate through file conventions on the shared BRAIN volume.

### Decisions

- **Own Kustomize manifests over Helm chart** — full control, consistent with all other homelab apps; init container logic ported from the Helm chart's shell scripts
- **Shared file-based BRAIN over database** — simpler, agents natively work with files, no extra infrastructure; can add a DB layer later if indexing/search becomes needed
- **One namespace for all agents** — shared CiliumNetworkPolicy, shared brain PVC, simpler RBAC; agents are logically separated by labels and naming convention
- **SMB ReadWriteMany for BRAIN** — the only storage option supporting multi-pod concurrent writes; private workspaces can use `local-path` for performance
- **Convention-based coordination over central service** — `_inbox/_outbox` pattern defers orchestrator complexity; agents can start collaborating through file conventions immediately
- **CiliumNetworkPolicy over vanilla NetworkPolicy** — matches existing homelab pattern and provides L7 filtering capabilities; blocks all LAN/RFC1918 egress given OpenClaw's security risk profile
- **Merge config mode** — UI changes (device pairings, settings tweaks) persist across restarts; ArgoCD ignores ConfigMap drift
- **Home-only access** — no `exposure: internet` label, no TLS needed, accessed via `envoy-gateway-home`
- **Defer management plane** — BRAIN infrastructure and file conventions are sufficient to start; orchestration service can be built later as a separate homelab app that reads the BRAIN registry and dispatches via agents' gateway APIs

### Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │              openclaw namespace              │
                         │                                              │
  openclaw.home.         │  ┌──────────────────────┐                   │
  timosur.com    ───────►│  │  research-deployment  │                   │
  (envoy-gateway-home)   │  │  ┌────────┐ ┌──────┐ │                   │
                         │  │  │openclaw│ │chrome │ │                   │
                         │  │  │ :18789 │ │ :9222 │ │                   │
                         │  │  └───┬────┘ └──────┘ │                   │
                         │  │      │                │                   │
                         │  └──────┼────────────────┘                   │
                         │         │                                    │
                         │    ┌────┴────┐    ┌──────────────────────┐   │
                         │    │ private │    │   brain-pvc (RWX)    │   │
                         │    │   PVC   │    │   SMB shared volume  │   │
                         │    │  (RWO)  │    │                      │   │
                         │    └─────────┘    │  Mounted at:         │   │
                         │                   │  /brain in init      │   │
                         │  ┌─ ─ ─ ─ ─ ─┐   │  /...workspace/brain │   │
                         │    future:        │    in main container  │   │
                         │  │ coding-    │   │                      │   │
                         │   deployment      │  Shared across all   │   │
                         │  │            │   │  agent deployments   │   │
                         │   ─ ─ ─ ─ ─ ─    └──────────────────────┘   │
                         │                                              │
                         │  ┌──────────────────────────────────────┐   │
                         │  │  CiliumNetworkPolicy                 │   │
                         │  │  - Ingress: envoy-gateway only       │   │
                         │  │  - Egress: public internet only      │   │
                         │  │    (blocks RFC1918/LAN)              │   │
                         │  └──────────────────────────────────────┘   │
                         └──────────────────────────────────────────────┘
```

---

## Steps

### 1. Create SMB StorageClass for Synology (actual SMB)

Add a new `StorageClass` using the already-deployed `smb.csi.k8s.io` provisioner (not the Synology iSCSI one). This goes in `apps/openclaw/storage-class.yaml`:

- `provisioner: smb.csi.k8s.io`
- `source: //192.168.1.26/<share-name>` (a dedicated SMB share created on the Synology)
- `mountOptions: [dir_mode=0777, file_mode=0777, uid=1000, gid=1000]` — OpenClaw runs as UID 1000
- Credentials secret referenced via `csi.storage.k8s.io/node-stage-secret-name`
- `reclaimPolicy: Retain`, `volumeBindingMode: Immediate`

**Pre-req:** Create the SMB share on the Synology NAS and store SMB creds in Azure Key Vault.

### 2. Create `apps/openclaw/namespace.yaml`

Namespace `openclaw` with Pod Security labels:

- `pod-security.kubernetes.io/enforce: baseline`
- `pod-security.kubernetes.io/audit: restricted`
- `pod-security.kubernetes.io/warn: restricted`
- No `exposure: internet` label (home-only)

### 3. Design the BRAIN directory structure

The shared BRAIN volume follows a convention that agents discover and respect:

```
/brain/
├── _registry/
│   └── agents.json              # Agent registry: id, role, capabilities, status
├── _inbox/
│   ├── <agent-id>/              # Per-agent task inbox (other agents drop tasks here)
│   └── ...
├── _outbox/
│   └── <agent-id>/              # Per-agent completed work / handoff artifacts
├── knowledge/
│   ├── topics/
│   │   ├── <topic-slug>.md      # Shared knowledge articles (any agent can read/write)
│   │   └── ...
│   ├── decisions/
│   │   └── <date>-<title>.md    # Architectural decisions, conclusions
│   └── index.json               # Knowledge index for quick lookup
├── context/
│   ├── projects/
│   │   └── <project>/           # Per-project shared context
│   └── global.md                # Cross-cutting context all agents should know
└── logs/
    └── <agent-id>/
        └── <date>.md            # Agent activity logs / summaries
```

This structure is enforced by convention (documented in a `BRAIN_PROTOCOL.md` that gets mounted into each agent's workspace). Agents can read anything, write to their own areas, and contribute to shared `knowledge/` and `context/`. The `_inbox/_outbox` pattern enables future orchestration without a central service.

### 4. Create `apps/openclaw/brain-pvc.yaml` (shared)

A `ReadWriteMany` PVC on the new SMB StorageClass:

- `accessModes: [ReadWriteMany]`
- `storageClassName: openclaw-smb`
- `storage: 10Gi`
- Mounted at `/brain` in init containers, and at `/home/node/.openclaw/workspace/brain` in main containers

### 5. Create `apps/openclaw/brain-init-configmap.yaml`

A ConfigMap containing:

- `BRAIN_PROTOCOL.md` — the agent collaboration protocol document (what the BRAIN is, directory conventions, how to read/write, how to register, how to hand off tasks)
- `init-brain.sh` — a shell script that creates the directory structure on first run and seeds `_registry/agents.json`

This ConfigMap is mounted by an init container in each agent's deployment to bootstrap the BRAIN structure.

### 6. Create per-agent manifests (starting with "research")

Following the multi-component pattern from `garden` and `bike-weather`, each agent gets prefixed files.

#### 6a. `apps/openclaw/research-configmap.yaml`

Agent-specific ConfigMap containing:

- `openclaw.json` with:
  - `gateway.trustedProxies: ["10.50.0.0/16", "10.51.0.0/16", "192.168.0.0/16"]`
  - `browser.enabled: true`, CDP URL `http://localhost:9222`
  - `agents.defaults.model.primary: "anthropic/claude-opus-4-6"`
  - `agents.defaults.userTimezone: "Europe/Berlin"`
  - Agent identity/role in the system prompt (e.g., "You are the Research Agent. Your role is...")
  - Discord channel config (this agent's channel/bot)
  - Reference to the BRAIN protocol: workspace includes `/brain`
- `bash_aliases` for exec convenience

#### 6b. `apps/openclaw/research-external-secret.yaml`

ExternalSecret → `openclaw-research-secrets` from Azure Key Vault (`ClusterSecretStore: azure-keyvault-store`):

- `ANTHROPIC_API_KEY` ← `openclaw-anthropic-api-key` (can be shared across agents)
- `OPENCLAW_GATEWAY_TOKEN` ← `openclaw-research-gateway-token` (unique per agent)
- `DISCORD_BOT_TOKEN` ← `openclaw-research-discord-bot-token` (unique per agent)

#### 6c. `apps/openclaw/research-pvc.yaml` (private)

Agent-private PVC for its `.openclaw` directory (config state, sessions, installed skills):

- `accessModes: [ReadWriteOnce]`
- `storageClassName: local-path` (private workspace, doesn't need to be shared)
- `storage: 5Gi`

#### 6d. `apps/openclaw/research-deployment.yaml`

Single-replica Deployment (`strategy: Recreate`) with labels `app: openclaw-research`, `app.kubernetes.io/part-of: openclaw`:

**Init containers:**

1. **`init-brain`** — `busybox:1.36` image. Runs `init-brain.sh` from the brain-init ConfigMap to ensure BRAIN directory structure exists and this agent is registered in `_registry/agents.json`. Mounts: brain PVC at `/brain`, brain-init ConfigMap at `/scripts`.

2. **`init-config`** — `ghcr.io/openclaw/openclaw:2026.2.26`. Merge-mode config initialization (ported from Helm chart). Copies ConfigMap's `openclaw.json` to the PVC, using Node.js deep merge logic to preserve runtime state (paired devices, settings) while applying manifest-defined config as overrides. Mounts: private PVC at `/home/node/.openclaw`, agent ConfigMap at `/config`, emptyDir at `/tmp`. Env: `CONFIG_MODE=merge`. SecurityContext: `runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true, readOnlyRootFilesystem: true, capabilities.drop: [ALL]`.

3. **`init-skills`** — `ghcr.io/openclaw/openclaw:2026.2.26`. Installs ClawHub skills (`weather`, `web-search`, `github`) via `npx clawhub install` into the PVC workspace. Idempotent — skips already-installed skills. Mounts: private PVC at `/home/node/.openclaw`, emptyDir at `/tmp`. Env: `HOME=/tmp, NPM_CONFIG_CACHE=/tmp/.npm`. Same security context.

**Containers:**

4. **`openclaw`** (main) — `ghcr.io/openclaw/openclaw:2026.2.26`. Command: `node dist/index.js gateway --bind lan --port 18789`. Mounts: private PVC at `/home/node/.openclaw`, brain PVC at `/home/node/.openclaw/workspace/brain`, ConfigMap bash_aliases at `/home/node/.bash_aliases`, emptyDir at `/tmp`. EnvFrom: `openclaw-research-secrets`. Resources: requests `200m/512Mi`, limits `1000m/2Gi`. Probes: TCP on 18789. SecurityContext: `runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true, readOnlyRootFilesystem: true, capabilities.drop: [ALL]`.

5. **`chromium`** (sidecar) — `chromedp/headless-shell:146.0.7680.31`. CDP on port 9222. Mounts: emptyDir at `/tmp`. Resources: requests `100m/256Mi`, limits `500m/1Gi`.

**Pod securityContext:** `fsGroup: 1000, fsGroupChangePolicy: OnRootMismatch`

#### 6e. `apps/openclaw/research-service.yaml`

ClusterIP Service `openclaw-research`, port 18789 targeting pod port 18789.

### 7. Create `apps/openclaw/cilium-network-policy.yaml`

A `CiliumNetworkPolicy` selecting all pods with label `app.kubernetes.io/part-of: openclaw`:

**Ingress:**
- Allow from `envoy-gateway-system` namespace on port 18789

**Egress:**
- Allow DNS to `kube-system` (UDP/TCP 53)
- Allow public internet (`0.0.0.0/0` except `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10`)
- Exception: Allow egress to Synology NAS `192.168.1.26` on port 445 (SMB) if needed for PVC mounts at pod level

This single policy covers all current and future agents in the namespace.

### 8. Create `apps/openclaw/kustomization.yaml`

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: openclaw
resources:
  - namespace.yaml
  - storage-class.yaml
  - brain-pvc.yaml
  - brain-init-configmap.yaml
  - research-configmap.yaml
  - research-external-secret.yaml
  - research-pvc.yaml
  - research-deployment.yaml
  - research-service.yaml
  - cilium-network-policy.yaml
```

### 9. Create `apps/_argocd/openclaw-app.yaml`

ArgoCD Application:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: openclaw
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/timosur/homelab.git
    targetRevision: HEAD
    path: apps/openclaw
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
  ignoreDifferences:
    - group: ""
      kind: ConfigMap
      name: openclaw-research-config
      jsonPointers:
        - /data
```

Add `openclaw-app.yaml` to `apps/_argocd/kustomization.yaml`.

### 10. Create HTTPRoute

Create `networking/httproutes/home/openclaw.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openclaw-home
  namespace: openclaw
spec:
  parentRefs:
    - name: envoy-gateway-home
      namespace: envoy-gateway-system
  hostnames:
    - "openclaw.home.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: openclaw-research
          port: 18789
```

Future agents can get their own hostnames (e.g. `openclaw-coding.home.timosur.com`) or path-based routing.

Register in `networking/httproutes/home/kustomization.yaml`.

### 11. Add secrets to Azure Key Vault

Add to `homelab-timosur` vault:

- `openclaw-anthropic-api-key` — Anthropic API key
- `openclaw-research-gateway-token` — strong random token for gateway pairing
- `openclaw-research-discord-bot-token` — Discord bot token for the research agent
- `openclaw-smb-username` / `openclaw-smb-password` — Synology SMB credentials

### 12. Synology NAS setup

- Create a dedicated SMB shared folder (e.g. `openclaw`)
- Create/assign a service account with read/write access
- Ensure SMB3 protocol is enabled

---

## Adding a New Agent

When ready to add a second agent (e.g. "coding"):

1. **Create manifests** — copy from `research-*`, change the role, system prompt, skills, Discord bot:
   - `coding-configmap.yaml`
   - `coding-external-secret.yaml`
   - `coding-pvc.yaml`
   - `coding-deployment.yaml`
   - `coding-service.yaml`
2. **Add secrets** to Azure Key Vault:
   - `openclaw-coding-gateway-token`
   - `openclaw-coding-discord-bot-token`
3. **Update `kustomization.yaml`** — add the new files
4. **Optionally add HTTPRoute** — new hostname or path rule
5. The new agent's `init-brain` container auto-registers in the shared BRAIN and inherits the full knowledge base

The CiliumNetworkPolicy, brain PVC, SMB StorageClass, and brain-init ConfigMap are **shared** — no changes needed.

---

## Verification

1. `kubectl get pods -n openclaw` — expect 1 pod (research) with 2 containers Running
2. `kubectl exec -n openclaw deploy/openclaw-research -c openclaw -- ls /home/node/.openclaw/workspace/brain/` — should show BRAIN directory structure
3. `kubectl exec -n openclaw deploy/openclaw-research -c openclaw -- cat /home/node/.openclaw/workspace/brain/_registry/agents.json` — should list the research agent
4. Access `http://openclaw.home.timosur.com/` — pair device, verify Discord, test BRAIN access
5. Verify CiliumNetworkPolicy blocks LAN: `kubectl exec ... -- curl -s --max-time 3 http://192.168.1.1` should fail
6. Verify external API works: `kubectl exec ... -- curl -s https://api.anthropic.com` should succeed

---

## File Manifest

```
apps/openclaw/
├── kustomization.yaml
├── namespace.yaml
├── storage-class.yaml
├── brain-pvc.yaml
├── brain-init-configmap.yaml
├── research-configmap.yaml
├── research-external-secret.yaml
├── research-pvc.yaml
├── research-deployment.yaml
├── research-service.yaml
└── cilium-network-policy.yaml

apps/_argocd/
└── openclaw-app.yaml              (new)

networking/httproutes/home/
└── openclaw.yaml                  (new)
```
