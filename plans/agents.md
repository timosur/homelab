# Plan: K8s-Native Sandboxed Agent Platform

**TL;DR**: Replace the OpenClaw plan with a Kubernetes-native agent execution platform. Ephemeral K8s Jobs run Claude Code CLI backed by local Ollama models (nemotron-3-nano on GPU node). Cilium provides network isolation, Tetragon provides process/file/per-binary-network enforcement (full OpenShell-equivalent). ProductHub (React + FastAPI) dispatches tasks, a ProductHub MCP server lets agents interact with the task dashboard. Agents create PRs on GitHub.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  ProductHub (React + FastAPI)                                            │
│  - Task dashboard with PRDs, product info                                │
│  - Triggers agent jobs via K8s API                                       │
│  - Monitors job status, collects results                                 │
└──────────┬───────────────────────────────────────────────────────────────┘
           │ creates K8s Job + ConfigMap (task, skills, instructions)
           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  agents namespace                                                        │
│                                                                          │
│  ┌─────────────────────────────────────────────┐                        │
│  │  K8s Job: agent-task-<id>                   │                        │
│  │  ┌───────────────────────────────────────┐  │                        │
│  │  │ Container: claude-agent               │  │                        │
│  │  │ - Claude Code CLI (ollama launch)     │  │                        │
│  │  │ - git, gh CLI, curl                   │  │                        │
│  │  │ - Mounts: task ConfigMap, GitHub PAT  │  │                        │
│  │  │ - OLLAMA_HOST → wol-proxy:11434       │  │                        │
│  │  └───────────────────────────────────────┘  │                        │
│  └─────────────────────────────────────────────┘                        │
│                                                                          │
│  ┌─────────────────────────────────┐  ┌───────────────────────────────┐ │
│  │ CiliumNetworkPolicy             │  │ Tetragon TracingPolicy        │ │
│  │ - Egress: wol-proxy, github,    │  │ - Process: allow list only    │ │
│  │   ProductHub MCP, DNS           │  │ - File: r/o root, r/w /workspace │
│  │ - Block all RFC1918             │  │ - Network: per-binary rules   │ │
│  └─────────────────────────────────┘  └───────────────────────────────┘ │
│                                                                          │
│  ┌─────────────────────────────────┐                                    │
│  │ ProductHub MCP Server (sidecar  │                                    │
│  │ or cluster service)             │                                    │
│  │ - SSE-based MCP protocol        │                                    │
│  │ - Read tasks, post comments,    │                                    │
│  │   update status, assign people  │                                    │
│  └─────────────────────────────────┘                                    │
└──────────────────────────────────────────────────────────────────────────┘
           │
           │ Ollama inference
           ▼
┌──────────────────────┐        ┌─────────────────────┐
│ wol-proxy:11434      │  WoL   │ GPU node             │
│ (wakes GPU on demand)│──────►│ 192.168.2.47:11434   │
│                      │        │ nemotron-3-nano:4b   │
└──────────────────────┘        └─────────────────────┘
```

## Steps

### Phase 1: Tetragon Deployment (new infra)

**1.1** Create `apps/tetragon/` with Kustomize overlay for Tetragon Helm chart  
- Follow cert-manager's Helm+overlay pattern (`sources:` in ArgoCD app)  
- Helm chart: `tetragon` from `helm.cilium.io` repo  
- Deploy into `kube-system` (Tetragon needs host access for eBPF)  
- Enable `TracingPolicy` CRD  
- Relevant: [apps/_argocd/cert-manager-app.yaml](apps/_argocd/cert-manager-app.yaml) as template for Helm+overlay ArgoCD app  

**1.2** Create `apps/_argocd/tetragon-app.yaml`, register in [apps/_argocd/kustomization.yaml](apps/_argocd/kustomization.yaml)

### Phase 2: Agent Base Image

**2.1** Create `agents/claude-agent/Dockerfile` — custom slim image:  
- Base: `debian:bookworm-slim` (multi-arch: amd64 + arm64)  
- Install: `git`, `gh` (GitHub CLI), `curl`, `jq`, `openssh-client`  
- Install: Ollama CLI (for `ollama launch claude`)  
- Install: Node.js (required by Claude Code CLI)  
- Install: Claude Code CLI via npm  
- Create non-root user `agent` (UID 1000)  
- Entrypoint script that: configures OLLAMA_HOST, runs `ollama launch claude` with the task prompt from mounted ConfigMap  
- Relevant: [agents/ollama/Dockerfile](agents/ollama/Dockerfile) as reference  

**2.2** Create `agents/claude-agent/entrypoint.sh`:  
- Read task config from `/task/config.json` (mounted ConfigMap)  
- Set OLLAMA_HOST from env  
- Set `--model` from config  
- Set `--dangerously-skip-permissions` (non-interactive agent mode)  
- Pass task prompt from config as the instruction  
- On completion, write result summary to `/workspace/result.json`  

**2.3** CI: GitHub Actions workflow to build + push to `ghcr.io/timosur/homelab/claude-agent:sha-<commit>@sha256:<digest>` (multi-arch)

### Phase 3: Agent Namespace & Base Manifests

**3.1** Create `apps/agents/namespace.yaml` — namespace `agents`, Pod Security `baseline`/`restricted`  

**3.2** Create `apps/agents/external-secret.yaml` — GitHub PAT for PR workflow:  
- `secretStoreRef: azure-keyvault-store` (ClusterSecretStore)  
- Azure KV key: `agents-github-pat`  
- Secret name: `agents-github-credentials`  
- Type: `Opaque` with `GITHUB_TOKEN` key  

**3.3** Create `apps/agents/serviceaccount.yaml` — ServiceAccount `agent-runner` with minimal RBAC (no cluster access, just for pod identity)  

**3.4** Create `apps/agents/kustomization.yaml` with base resources  

**3.5** Create `apps/_argocd/agents-app.yaml`, register in kustomization  

### Phase 4: Cilium Network Policy

**4.1** Create `apps/agents/cilium-network-policy.yaml`:  
- **Selector**: all pods in `agents` namespace (label `app.kubernetes.io/part-of: agents`)  
- **Egress allow**:  
  - DNS → `kube-system` (UDP/TCP 53)  
  - Ollama → `wol-proxy.wol-proxy.svc.cluster.local:11434`  
  - GitHub → `github.com:443`, `api.github.com:443`, `*.githubusercontent.com:443`  
  - ProductHub MCP → `producthub-mcp.agents.svc.cluster.local:<port>` (in-cluster)  
- **Egress deny**: all RFC1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) except the above  
- **Ingress**: deny all (Jobs don't serve traffic)  

**4.2** Update `apps/wol-proxy/allow-ingress-from-open-webui.yaml` → rename/extend to allow ingress from **both** `open-webui` and `agents` namespaces on port 11434  
- Relevant: [apps/wol-proxy/allow-ingress-from-open-webui.yaml](apps/wol-proxy/allow-ingress-from-open-webui.yaml)  

### Phase 5: Tetragon TracingPolicies (OpenShell-equivalent sandboxing)

**5.1** Create `apps/agents/tetragon-base-policy.yaml` — `TracingPolicy` for all agent pods:  

- **Process enforcement** (`kprobe` on `execve`): allow-list only  
  - `/usr/bin/git`, `/usr/bin/gh`, `/usr/bin/curl`, `/usr/bin/node`, `/bin/bash`, `/bin/sh`, `/usr/bin/jq`, `/usr/local/bin/claude`, `/usr/local/bin/ollama`  
  - Kill any process not in the allow-list (`sigkill` action)  

- **File access enforcement** (`kprobe` on `open/openat`):  
  - Read-only: `/usr`, `/lib`, `/etc`, `/proc`, `/dev/urandom`  
  - Read-write: `/workspace`, `/tmp`, `/dev/null`  
  - Deny: everything else (block writes to system paths)  

- **Network enforcement** (per-binary, `kprobe` on `connect`):  
  - `git`/`gh`: only `github.com:443`, `api.github.com:443`  
  - `curl`: only allowed API endpoints (configurable)  
  - `claude`/`node`: only Ollama endpoint (wol-proxy ClusterIP), ProductHub MCP  
  - `ollama`: only Ollama endpoint  
  - Deny all other outbound connections  

**5.2** Create skill-specific TracingPolicy variants (applied per-job via labels):  

- **`tetragon-skill-git-pr.yaml`**: extends base, allows `git push`, `gh pr create`  
- **`tetragon-skill-git-readonly.yaml`**: base only, `git clone`/`fetch` allowed, `push` blocked  
- **`tetragon-skill-web-search.yaml`**: extends base, allows `curl` to broader domain set  

Tetragon TracingPolicies use `podSelector` with labels like `agent-skill: git-pr` to bind policies to specific Jobs.  

### Phase 6: ProductHub Integration

**6.1** Define ProductHub MCP server as SSE-based (HTTP transport) — runs as a Deployment in `agents` namespace  
- Exposes tools: `get_task`, `update_task_status`, `post_comment`, `assign_task`, `get_prd`  
- Authenticates agent pods via ServiceAccount token or a shared secret  
- ClusterIP service: `producthub-mcp.agents.svc.cluster.local`  

**6.2** ProductHub FastAPI backend gets a `/api/agents/` endpoint group:  
- `POST /api/agents/dispatch` — creates a K8s Job with:  
  - ConfigMap containing: task ID, prompt/instructions, model name, skill labels, timeout  
  - Job spec referencing agent base image, mounted secrets, skill labels  
  - Tetragon policy selection via pod labels  
- `GET /api/agents/status/{job-id}` — polls Job status  
- `GET /api/agents/result/{job-id}` — reads result from completed Job's output  
- `DELETE /api/agents/cancel/{job-id}` — deletes running Job  

**6.3** ProductHub needs RBAC in the `agents` namespace:  
- ServiceAccount `producthub-agent-dispatcher` with Role granting:  
  - `create`, `get`, `list`, `delete` on `batch/v1 Jobs`  
  - `create`, `get`, `delete` on `v1 ConfigMaps` (task configs)  
  - `get`, `list` on `v1 Pods` and `v1 Pods/log` (for status/output)  

### Phase 7: Job Template & Execution Flow

**7.1** Define the K8s Job template that ProductHub creates per task:  

```
Job: agent-task-<task-id>
  labels:
    app.kubernetes.io/part-of: agents
    agent-skill: <skill-profile>  ← selects Tetragon policy
  spec:
    backoffLimit: 0
    activeDeadlineSeconds: 3600  (configurable timeout)
    template:
      initContainers:
        - name: clone-repo
          image: alpine/git
          command: git clone <repo-url> /workspace/repo
          volumeMounts: [workspace emptyDir]
      containers:
        - name: claude-agent
          image: ghcr.io/timosur/homelab/claude-agent:<pinned>
          env:
            - OLLAMA_HOST: http://ollama-service.wol-proxy.svc.cluster.local:11434
            - GITHUB_TOKEN: from secret
            - CLAUDE_MODEL: nemotron-3-nano:4b
          volumeMounts:
            - /task/config.json ← task ConfigMap
            - /workspace ← emptyDir (clone target + work area)
          resources:
            requests: 200m/512Mi
            limits: 1000m/2Gi
          securityContext:
            runAsUser: 1000
            runAsNonRoot: true
            readOnlyRootFilesystem: true
            capabilities.drop: [ALL]
```

**7.2** Entrypoint execution flow:  
1. Read `/task/config.json` (task prompt, skill config, repo URL)  
2. `cd /workspace/repo`  
3. `ollama launch claude --model $CLAUDE_MODEL -- --dangerously-skip-permissions "$TASK_PROMPT"`  
4. On exit: write `/workspace/result.json` with status, PR URL if created, summary  
5. Job completes → ProductHub polls status, reads logs  

### Phase 8: WoL Integration

**8.1** Before dispatching a Job, ProductHub calls wol-proxy health endpoint to ensure GPU node is awake  
- If GPU node is down, wol-proxy wakes it (existing functionality)  
- ProductHub waits for Ollama to be ready before creating the Job  
- Alternative: agent entrypoint retries Ollama connection with backoff  

### Phase 9: Wiring & ArgoCD

**9.1** Create `apps/agents/kustomization.yaml` combining all manifests  
**9.2** Register in ArgoCD app-of-apps  
**9.3** ProductHub deployment (separate app: `apps/producthub/`) — deferred, already in development  

## Relevant Files

- [agents/ollama/Dockerfile](agents/ollama/Dockerfile) — reference for agent image build pattern  
- [agents/ollama/policy.yaml](agents/ollama/policy.yaml) — OpenShell policy to translate into Tetragon TracingPolicies  
- [apps/wol-proxy/](apps/wol-proxy/) — Ollama proxy, needs ingress policy update for `agents` namespace  
- [apps/wol-proxy/allow-ingress-from-open-webui.yaml](apps/wol-proxy/allow-ingress-from-open-webui.yaml) — extend for agents  
- [apps/_argocd/cert-manager-app.yaml](apps/_argocd/cert-manager-app.yaml) — Helm+overlay ArgoCD pattern for Tetragon  
- [networking/cilium-network-policies/](networking/cilium-network-policies/) — existing Cilium policy patterns  
- [plans/agents.md](plans/agents.md) — to be replaced with this plan  

## Verification

1. **Tetragon**: `kubectl get tracingpolicies` — confirms CRDs are available; `kubectl logs -n kube-system ds/tetragon` — agent healthy  
2. **Agent image**: build locally, `docker run --rm claude-agent echo ok` — image works  
3. **Network isolation**: create a test Job in `agents` namespace, verify:  
   - `curl ollama-service.wol-proxy.svc.cluster.local:11434/api/tags` → succeeds  
   - `curl 192.168.1.1` → blocked by CiliumNetworkPolicy  
   - `curl github.com` → succeeds  
4. **Tetragon enforcement**: in test Job, verify:  
   - `python3 -c "print('hello')"` → killed (not in allow-list)  
   - `git clone https://github.com/timosur/homelab.git` → succeeds  
   - `curl https://evil.com` → blocked by Tetragon  
5. **End-to-end**: ProductHub dispatches a task "Clone homelab repo, read README, summarize in a PR comment" → Job runs, agent completes, ProductHub shows result  
6. **WoL**: dispatch task with GPU node off → wol-proxy wakes it → agent runs after Ollama becomes available  

## Decisions

- **Shared namespace `agents`** — all Jobs share one namespace, Tetragon policies differentiate via labels  
- **No shared BRAIN volume** — Jobs are ephemeral; results stored in ProductHub (via MCP) and GitHub (via PRs)  
- **ProductHub MCP server as SSE** — HTTP transport, runs in-cluster, agents connect via ClusterIP service  
- **WoL handled before dispatch** — ProductHub ensures GPU node is awake before creating Job, not the agent's responsibility  

## Further Considerations

1. **Model selection flexibility**: Should agents be able to use different Ollama models per task (e.g., nemotron for code, llama for research)?  Recommendation: yes, make `CLAUDE_MODEL` a field in the task config.  
2. **Job output persistence**: After Job completion, logs and `/workspace/result.json` are only available until pod garbage collection. Recommendation: ProductHub copies results before cleanup, or use a sidecar that pushes to S3/NAS.  
3. **Concurrency limits**: Multiple agent Jobs running simultaneously will contend for GPU node Ollama. Recommendation: start with sequential dispatch (one Job at a time), add a semaphore ConfigMap or queue later.  
