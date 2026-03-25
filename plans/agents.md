# Plan: K8s-Native Agent Platform (kagent + AgentGateway)

**TL;DR**: Kubernetes-native agent platform built on the Solo.io open-source stack. **kagent** (CNCF sandbox) provides declarative Agent CRDs, conversation engine (Go ADK), built-in UI, and observability. **AgentGateway** provides unified LLM/MCP gateway with token budgets, model routing, and observability — all LLM consumers (kagent, Open-WebUI) route through it. kagent's engine drives coding directly via a custom **Coding Tools MCP Server** (Go, built with kmcp) providing file CRUD, regex code search, shell execution, Git operations, GitHub CLI, and web/doc fetching — single LLM loop, no wrapper layers, full local workspace. **ProductHub selects the model per task** — multiple ModelConfigs in kagent, AgentGateway routes to the right backend. **Cilium** provides L3/L4 network isolation (all HTTPS egress allowed). **Tetragon** provides process allow-listing and file access control. Single-task execution only. Agents create PRs on GitHub. GPU node serves Ollama models via wol-proxy (WoL on demand).

**agentregistry** is intentionally skipped — GitOps is sufficient governance at homelab scale.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  ProductHub (React + FastAPI)                                       │
│  - Sends coding tasks as conversations to kagent API                │
│  - Monitors agent sessions, collects results                        │
└──────────┬──────────────────────────────────────────────────────────┘
           │ kagent REST/WebSocket API
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  agents namespace                                                   │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ kagent Controller (Go)                                        │  │
│  │ - Watches Agent, ModelConfig, MCPServer/RemoteMCPServer CRDs  │  │
│  │ - Creates/manages agent Deployments                           │  │
│  │ - Reconciles tool references                                  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ kagent Engine (Go ADK runtime, ~2s startup)                   │  │
│  │ - Runs coding-agent conversation loop (single LLM loop)       │  │
│  │ - Calls Ollama via AgentGateway for reasoning                 │  │
│  │ - Uses coding-tools MCP for file/git/shell operations         │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ kagent UI (web dashboard, port 8080)                          │  │
│  │ - Agent management, conversation viewer, tool call traces     │  │
│  │ - HITL approval gates for destructive operations              │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────┐  ┌─────────────────────────────┐ │
│  │ ModelConfig CRDs (one per    │  │ Agent CRD: coding-agent     │ │
│  │ model, selected by           │  │ runtime: go                 │ │
│  │ ProductHub per task):        │  │ tools: coding-tools-mcp     │ │
│  │ - ollama-nano (4b)           │  │ skills: [pr-workflow, ...]  │ │
│  │ - ollama-large (8b+)         │  │                             │ │
│  │ All → AgentGateway → Ollama  │  │                             │ │
│  └──────────────────────────────┘  └─────────────────────────────┘ │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ CloudNative-PG: kagent-postgres                               │  │
│  │ - kagent conversation store (no pgvector — memory disabled)   │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌────────────────────────────┐  ┌──────────────────────────────┐  │
│  │ CiliumNetworkPolicy        │  │ Tetragon TracingPolicy       │  │
│  │ - Egress: AgentGateway,    │  │ - Process: allow-list only   │  │
│  │   mcp ns, K8s API, DNS     │  │   (kagent binaries,          │  │
│  │ - Ingress: ProductHub,     │  │    postgres)                 │  │
│  │   envoy-gateway-home       │  │ - File: r/o root, r/w        │  │
│  │ - Deny all else            │  │   postgres data + /tmp       │  │
│  └────────────────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
           │
           │ MCP tool calls (Streamable HTTP)
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  mcp namespace (sandboxed MCP tool server)                          │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ Coding Tools MCP Server (Deployment + ClusterIP)              │  │
│  │ - Go binary (built with kmcp), Streamable HTTP on :8080       │  │
│  │ - Per-workspace temp dir /workspace/<uuid>/                   │  │
│  │                                                               │  │
│  │   FILE OPERATIONS                                             │  │
│  │   workspace_init(repo_url, branch) → clone repo, return id   │  │
│  │   read_file(workspace_id, path) → file contents              │  │
│  │   write_file(workspace_id, path, content) → create/overwrite │  │
│  │   edit_file(workspace_id, path, old, new) → find-replace     │  │
│  │   rename_file(workspace_id, old_path, new_path) → move/rename│  │
│  │   delete_file(workspace_id, path) → remove file              │  │
│  │   list_directory(workspace_id, path) → entries                │  │
│  │                                                               │  │
│  │   SEARCH / CODEBASE INDEXING                                  │  │
│  │   search_files(workspace_id, regex, path?) → Go regexp matches│  │
│  │   search_filenames(workspace_id, glob) → matching file paths  │  │
│  │                                                               │  │
│  │   TERMINAL & GIT                                              │  │
│  │   run_command(workspace_id, cmd) → stdout/stderr (sandboxed)  │  │
│  │   git_status(workspace_id) → working tree status              │  │
│  │   git_diff(workspace_id) → unified diff                       │  │
│  │   git_commit(workspace_id, message) → commit hash             │  │
│  │   git_push(workspace_id) → push to remote                    │  │
│  │   gh_pr_create(workspace_id, title, body) → PR URL           │  │
│  │                                                               │  │
│  │   WEB / DOCUMENTATION                                         │  │
│  │   fetch_url(url) → page content (HTML→text)                  │  │
│  │                                                               │  │
│  │   LIFECYCLE                                                   │  │
│  │   workspace_cleanup(workspace_id) → remove temp dir           │  │
│  │                                                               │  │
│  │ - Service: appProtocol: agentgateway.dev/mcp                  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ ProductHub MCP Server (Deployment + ClusterIP) — optional     │  │
│  │ - Tools: get_task, update_task_status, post_comment           │  │
│  │ - Registered as MCPServer CRD in kagent                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌────────────────────────────┐  ┌──────────────────────────────┐  │
│  │ CiliumNetworkPolicy        │  │ Tetragon TracingPolicy       │  │
│  │ - Egress: all HTTPS,       │  │ - Process: allow-list only   │  │
│  │   github.com:443, DNS      │  │   (git, gh, bash, sh, jq,    │  │
│  │ - Ingress: from agents ns, │  │    coding-tools-mcp)         │  │
│  │   from agentgateway-system │  │ - File: r/o root, r/w        │  │
│  │ - Deny all RFC1918         │  │   /workspace and /tmp        │  │
│  └────────────────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
           │
           │ LLM routing
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  agentgateway-system namespace                                      │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ AgentGateway Proxy (Rust data plane)                          │  │
│  │                                                                │  │
│  │  Gateway: agentgateway-proxy (class: agentgateway, port 80)   │  │
│  │                                                                │  │
│  │  Consumers: kagent engine, Open-WebUI, ProductHub             │  │
│  │                                                                │  │
│  │  HTTPRoute /ollama → AgentgatewayBackend (OpenAI-compat)      │  │
│  │    → headless svc → wol-proxy:11434 → GPU node 192.168.2.47  │  │
│  │    (multiple model backends, ProductHub selects per task)     │  │
│  │                                                                │  │
│  │  HTTPRoute /mcp/* → AgentgatewayBackend (MCP targets)         │  │
│  │    → coding-tools-mcp.mcp.svc                                 │  │
│  │    → producthub-mcp.mcp.svc                                   │  │
│  │                                                                │  │
│  │  AgentgatewayPolicy:                                          │  │
│  │  - Token rate limiting per consumer                           │  │
│  │  - OpenTelemetry metrics, logs, traces                        │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
           │
           │ Ollama inference
           ▼
┌──────────────────────┐        ┌─────────────────────┐
│ wol-proxy:11434      │  WoL   │ GPU node             │
│ (wakes GPU on demand)│───────►│ 192.168.2.47:11434   │
│                      │        │ nemotron-3-nano:4b   │
└──────────────────────┘        └─────────────────────┘
```

## Component Stack

| Layer                    | Component                          | Role                                                      |
| ------------------------ | ---------------------------------- | --------------------------------------------------------- |
| **Agent framework**      | kagent (CNCF sandbox, v0.8.0)      | Agent CRDs, ADK engine, UI, observability                 |
| **LLM/MCP gateway**      | AgentGateway (v1.0.0)              | L7 proxy, token budgets, rate limiting, OTel              |
| **Process/file sandbox** | Tetragon (v1.3.0)                  | eBPF process allow-list, file access control              |
| **Network sandbox**      | Cilium (existing)                  | L3/L4 isolation, egress deny RFC1918                      |
| **Coding tools**         | Coding Tools MCP Server (Go, kmcp) | File CRUD, code search, shell, git, GitHub CLI, web fetch |
| **LLM inference**        | Ollama on GPU node (existing)      | nemotron-3-nano:4b via wol-proxy                          |

## Steps

### Phase 1a: Tetragon Deployment (new infra)

**1a.1** Create `apps/tetragon/kustomization.yaml` — empty overlay (placeholder for future overrides)

**1a.2** Create `apps/_argocd/tetragon-app.yaml` — Helm+overlay ArgoCD Application:
- Chart: `tetragon` from `https://helm.cilium.io`, version `v1.3.0`
- Deploy into `kube-system` (Tetragon needs host access for eBPF)
- Helm values: enable `TracingPolicy` CRD
- `ServerSideApply=true` (CRD-heavy chart)

**1a.3** Register `tetragon-app.yaml` in `apps/_argocd/kustomization.yaml`

### Phase 1b: AgentGateway Deployment (new infra)

AgentGateway ships as two Helm charts (CRDs + main), both from OCI registry `cr.agentgateway.dev/charts`.

**1b.1** Create `apps/agentgateway/kustomization.yaml` — overlay with backend + route + gateway manifests

**1b.2** Create `apps/_argocd/agentgateway-crds-app.yaml`:
- Chart: `agentgateway-crds` from `cr.agentgateway.dev/charts`, version `v1.0.0`
- Namespace: `agentgateway-system`
- `ServerSideApply=true`

**1b.3** Create `apps/_argocd/agentgateway-app.yaml`:
- Chart: `agentgateway` from `cr.agentgateway.dev/charts`, version `v1.0.0`
- Namespace: `agentgateway-system`
- Overlay path: `apps/agentgateway`

**1b.4** Create overlay resources in `apps/agentgateway/`:
- `gateway.yaml` — Gateway resource (class `agentgateway`, listener HTTP port 80, allowedRoutes from All namespaces)
- `ollama-backend.yaml` — AgentgatewayBackend for Ollama:
  ```yaml
  apiVersion: agentgateway.dev/v1alpha1
  kind: AgentgatewayBackend
  metadata:
    name: ollama
    namespace: agentgateway-system
  spec:
    ai:
      provider:
        openai:
          model: nemotron-3-nano:4b
        host: ollama.agentgateway-system.svc.cluster.local
        port: 11434
  ```
  Plus headless Service + EndpointSlice pointing at `ollama-service.wol-proxy.svc.cluster.local:11434`
- `ollama-route.yaml` — HTTPRoute for LLM traffic → ollama backend
- `coding-tools-mcp-backend.yaml` — AgentgatewayBackend (MCP target, static host: `coding-tools-mcp.mcp.svc.cluster.local`)
- `coding-tools-mcp-route.yaml` — HTTPRoute `/mcp/coding-tools` → MCP backend

**1b.5** Register both `agentgateway-crds-app.yaml` and `agentgateway-app.yaml` in `apps/_argocd/kustomization.yaml`

**1b.6** Prerequisite check: Verify Gateway API CRDs v1.5.0 are installed (likely already present via Envoy Gateway). If not, install via `kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml`

**1b.7** Migrate Open-WebUI to route through AgentGateway:
- Update `OLLAMA_BASE_URL` in `apps/open-webui/configmap.yaml` from `http://ollama-service.wol-proxy.svc.cluster.local:11434` to `http://agentgateway-proxy.agentgateway-system.svc:80/ollama`
- This gives unified LLM visibility — all consumers (kagent, Open-WebUI, ProductHub) go through AgentGateway for token tracking and observability

### Phase 2: kagent Deployment (new — replaces raw K8s Job infrastructure)

kagent ships as two Helm charts (`kagent-crds` + `kagent`), both from OCI registry `ghcr.io/kagent-dev/kagent/helm/`.

**2.1** Create `apps/agents/kustomization.yaml` — overlay with CRD resources (ModelConfig, Agent, etc.)

**2.2** Create `apps/_argocd/kagent-crds-app.yaml`:
- Chart: `kagent-crds` from `oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds`, version `v0.8.0`
- Namespace: `agents`
- `ServerSideApply=true`

**2.3** Create `apps/_argocd/kagent-app.yaml` — Helm+overlay:
- Chart: `kagent` from `oci://ghcr.io/kagent-dev/kagent/helm/kagent`, version `v0.8.0`
- Namespace: `agents`
- Overlay path: `apps/agents`
- Helm values:
  ```yaml
  providers:
    default: ollama
  kmcp:
    enabled: true
  ```

**2.4** Create `apps/agents/postgres.yaml` — CloudNative-PG Cluster for kagent:
```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: kagent-postgres
  namespace: agents
spec:
  instances: 1
  enablePDB: false
  postgresql:
    parameters:
      max_connections: "200"
      shared_buffers: "256MB"
  bootstrap:
    initdb:
      database: kagent
      owner: kagent
      secret:
        name: kagent-postgres-credentials
  storage:
    size: 10Gi
    storageClass: hcloud-volumes
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "500m"
```

**2.5** Create `apps/agents/external-secret.yaml` — PostgreSQL credentials:
- Azure KV keys: `kagent-postgres-username`, `kagent-postgres-password`
- Secret name: `kagent-postgres-credentials`, type: `kubernetes.io/basic-auth`

**2.6** Register `kagent-crds-app.yaml` and `kagent-app.yaml` in `apps/_argocd/kustomization.yaml`

### Phase 3: Coding Tools MCP Server

Full-featured MCP tool server that gives kagent's coding-agent a local workspace with file CRUD, code search, shell execution, git operations, and web fetching. Built with kmcp (Go). No second LLM — kagent's engine drives all reasoning in a single Ollama loop and calls these tools for side effects.

**3.1** Create `agents/coding-tools-mcp/main.go` — MCP server implementation (kmcp):
- Transport: Streamable HTTP on port 8080
- Health endpoint: `GET /health`
- Workspace lifecycle: temp dirs under `/workspace/<uuid>/`, cleaned up per-session
- Tools (grouped by capability):

  **File Operations:**
  - `workspace_init(repo_url, branch)` → `git clone --branch <branch> <repo_url> /workspace/<uuid>`, return `workspace_id`
  - `read_file(workspace_id, path)` → read file contents from workspace
  - `write_file(workspace_id, path, content)` → create or overwrite file in workspace
  - `edit_file(workspace_id, path, old_text, new_text)` → find-and-replace text in file (exact match, single occurrence)
  - `rename_file(workspace_id, old_path, new_path)` → rename or move file within workspace
  - `delete_file(workspace_id, path)` → remove file from workspace
  - `list_directory(workspace_id, path)` → list entries (files/dirs) with type indicators

  **Search / Codebase Indexing:**
  - `search_files(workspace_id, regex, path?)` → regex search across files using Go's built-in `regexp` + `filepath.Walk`, return matching lines with context. Optional `path` scopes search to subdirectory.
  - `search_filenames(workspace_id, glob)` → find files by name pattern (e.g. `*.go`, `*_test.py`)

  **Terminal & Git:**
  - `run_command(workspace_id, command)` → execute shell command in workspace dir, return stdout/stderr/exit_code. Tetragon enforces process allow-list.
  - `git_status(workspace_id)` → `git status --porcelain`, return working tree status
  - `git_diff(workspace_id)` → `git diff`, return unified diff of unstaged changes
  - `git_commit(workspace_id, message)` → `git add -A && git commit -m "<message>"`, return commit hash
  - `git_push(workspace_id)` → `git push origin HEAD`, return status
  - `gh_pr_create(workspace_id, title, body)` → `gh pr create --title --body`, return PR URL

  **Web / Documentation:**
  - `fetch_url(url)` → HTTP GET (Go `net/http`), convert HTML→plain text (strip tags, extract content), return text. Useful for reading docs and error message lookups. All HTTPS egress allowed via CiliumNetworkPolicy.

  **Lifecycle:**
  - `workspace_cleanup(workspace_id)` → remove `/workspace/<uuid>/`

**3.2** Create `agents/coding-tools-mcp/Dockerfile` — slim Go binary image:
- Multi-stage build: Go builder → `debian:bookworm-slim`
- Install runtime deps: `git`, `gh` (GitHub CLI), `jq`, `openssh-client`, `curl`
- Create non-root user `agent` (UID 1000)
- Binary: `/usr/local/bin/coding-tools-mcp`

**3.3** Create `apps/mcp/namespace.yaml` — namespace `mcp`

**3.4** Create `apps/mcp/deployment.yaml` — Coding Tools MCP Server:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: coding-tools-mcp
  namespace: mcp
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: coding-tools-mcp
  template:
    metadata:
      labels:
        app: coding-tools-mcp
        app.kubernetes.io/part-of: mcp
    spec:
      containers:
        - name: coding-tools-mcp
          image: ghcr.io/timosur/homelab/coding-tools-mcp:latest  # TODO: Pin
          ports:
            - containerPort: 8080
              protocol: TCP
          env:
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: mcp-github-credentials
                  key: GITHUB_TOKEN
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            runAsUser: 1000
            runAsNonRoot: true
            capabilities:
              drop: [ALL]
          volumeMounts:
            - name: workspace
              mountPath: /workspace
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 2
            periodSeconds: 10
      volumes:
        - name: workspace
          emptyDir:
            sizeLimit: 5Gi
```

**3.5** Create `apps/mcp/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: coding-tools-mcp
  namespace: mcp
  labels:
    app: coding-tools-mcp
spec:
  selector:
    app: coding-tools-mcp
  ports:
    - port: 80
      targetPort: 8080
      appProtocol: agentgateway.dev/mcp
```

**3.6** Create `apps/mcp/external-secret.yaml` — GitHub PAT for git push + PR creation:
- Azure KV key: `mcp-github-pat`
- Secret name: `mcp-github-credentials`, type: `Opaque` with `GITHUB_TOKEN` key

**3.7** Create `apps/mcp/kustomization.yaml` with all resources

**3.8** Create `apps/_argocd/mcp-app.yaml`, register in `apps/_argocd/kustomization.yaml`

**3.9** CI: GitHub Actions workflow to build + push `ghcr.io/timosur/homelab/coding-tools-mcp` (multi-arch)

### Phase 4: kagent Agent CRDs & Skills

**4.1** Create `apps/agents/model-configs.yaml` — Multiple ModelConfig CRDs (ProductHub selects per task):
```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: ollama-nano
  namespace: agents
spec:
  apiKeySecretKey: OPENAI_API_KEY
  apiKeySecret: kagent-ollama-dummy
  model: nemotron-3-nano:4b
  provider: Ollama
  ollama:
    host: http://agentgateway-proxy.agentgateway-system.svc:80/ollama
---
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: ollama-large
  namespace: agents
spec:
  apiKeySecretKey: OPENAI_API_KEY
  apiKeySecret: kagent-ollama-dummy
  model: <larger-model-tbd>  # e.g. qwen2.5-coder:7b, codellama:13b
  provider: Ollama
  ollama:
    host: http://agentgateway-proxy.agentgateway-system.svc:80/ollama
```
ProductHub specifies which agent (and thus which ModelConfig) to invoke per task based on complexity. Start with `ollama-nano` for simple tasks. Add larger models to Ollama and create corresponding ModelConfigs as needed.

**4.2** Create `apps/agents/coding-agent.yaml` — Agent CRD:
```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: coding-agent
  namespace: agents
spec:
  description: >-
    Coding agent that implements tasks, creates PRs, and reviews code
    on GitHub repositories. Has full local workspace with file CRUD,
    code search, shell execution, git, and web documentation access.
  type: Declarative
  declarative:
    runtime: go
    modelConfig: ollama-nano  # Default; ProductHub can invoke different agent variants
    systemMessage: |
      You are a coding agent with a full local workspace. When given a coding task:

      1. INIT: Use workspace_init to clone the repo and create a workspace.
      2. UNDERSTAND: Use search_files, search_filenames, read_file, and
         list_directory to explore the codebase. Understand existing patterns
         before making changes.
      3. RESEARCH: If you encounter unfamiliar APIs or error messages, use
         fetch_url to read official documentation.
      4. IMPLEMENT: Use write_file for new files, edit_file for modifications,
         rename_file or delete_file as needed.
      5. VERIFY: Use run_command to run tests, linters, or build commands.
         Use git_diff to review your changes.
      6. COMMIT: Use git_commit with a clear, conventional commit message.
      7. PUSH & PR: Use git_push and gh_pr_create to open a PR.
      8. CLEANUP: Use workspace_cleanup when done.

      Principles:
      - Always read existing code before modifying it.
      - Make small, focused changes. Test before committing.
      - Use search_files to find usages before renaming or deleting.
      - Prefer edit_file for surgical changes over write_file for full rewrites.
    tools:
      - type: McpServer
        mcpServer:
          name: coding-tools-mcp
          namespace: mcp
          kind: Service
          toolNames:
            - workspace_init
            - read_file
            - write_file
            - edit_file
            - rename_file
            - delete_file
            - list_directory
            - search_files
            - search_filenames
            - run_command
            - git_status
            - git_diff
            - git_commit
            - git_push
            - gh_pr_create
            - fetch_url
            - workspace_cleanup
    skills:
      gitRefs:
        - url: https://github.com/timosur/homelab.git
          ref: main
          path: agents/coding-tools-mcp/skills
    context:
      compaction:
        compactionInterval: 5
```

**4.3** Create `agents/coding-tools-mcp/skills/` directory with markdown skill files:
- `pr-workflow.md` — PR creation conventions, commit message format, branch naming
- `code-review.md` — review criteria, what to check for, comment style
- `task-decomposition.md` — how to break down complex tasks, when to split PRs

### Phase 5: Sandboxing (Cilium + Tetragon)

#### 5a: mcp namespace (MCP tool server)

**5a.1** Create `apps/mcp/cilium-network-policy.yaml`:
- **Selector**: all pods with label `app.kubernetes.io/part-of: mcp`
- **Egress allow**:
  - DNS → `kube-system` (UDP/TCP 53)
  - All HTTPS (port 443) — needed for `fetch_url` to access arbitrary documentation. GitHub (`github.com`, `api.github.com`) is the primary target but any doc host must be reachable.
- **Egress deny**: all RFC1918 (prevent lateral movement within cluster/LAN), non-HTTPS protocols
- **Ingress allow**:
  - From `agents` namespace (kagent engine → MCP tool calls)
  - From `agentgateway-system` namespace (if routing MCP via AgentGateway)
- **Ingress deny**: all else
- **Note**: No Ollama egress needed — coding-tools-mcp is a pure tool server, it never calls the LLM directly. Only kagent engine calls Ollama (via AgentGateway).

**5a.2** Create `apps/mcp/tetragon-policy.yaml` — TracingPolicy for MCP server pods:

- **Process enforcement** (`kprobe` on `execve`): allow-list only
  - `/usr/bin/git`, `/usr/bin/gh`, `/bin/bash`, `/bin/sh`, `/usr/bin/jq`
  - `/usr/local/bin/coding-tools-mcp`
  - Kill any process not in the allow-list (`sigkill` action)
  - Note: `search_files` and `fetch_url` are implemented in Go (no external binaries)

- **File access enforcement** (`kprobe` on `open/openat`):
  - Read-only: `/usr`, `/lib`, `/etc`, `/proc`, `/dev/urandom`
  - Read-write: `/workspace`, `/tmp`, `/dev/null`
  - Deny: everything else (block writes to system paths)

- **No network enforcement** — Cilium handles L3/L4, AgentGateway handles L7

#### 5b: agents namespace (agent engine + controller)

**5b.1** Create `apps/agents/cilium-network-policy.yaml`:
- **Selector**: all pods in `agents` namespace
- **Egress allow**:
  - DNS → `kube-system` (UDP/TCP 53)
  - AgentGateway → `agentgateway-system` namespace (TCP 80) — for LLM inference
  - MCP tool server → `mcp` namespace (TCP 80) — for tool calls
  - Kubernetes API server → `default` namespace or IP (TCP 443) — kagent controller needs to reconcile CRDs
  - CloudNative-PG → self-namespace (TCP 5432) — postgres within agents namespace
- **Egress deny**: all internet, all RFC1918 not in allow-list
- **Ingress allow**:
  - From `envoy-gateway-system` namespace (kagent UI exposed on home network via HTTPRoute)
  - From `envoy-gateway-internet-system` namespace (if ProductHub accesses kagent API from internet)
  - Note: internal pod-to-pod within agents namespace (engine ↔ postgres) is allowed by selector
- **Ingress deny**: all else

**5b.2** Create `apps/agents/tetragon-policy.yaml` — TracingPolicy for kagent pods:

- **Process enforcement** (`kprobe` on `execve`): allow-list only
  - kagent controller/engine/UI Go binaries (single binary, multiple entrypoints)
  - PostgreSQL binaries (`/usr/lib/postgresql/*/bin/postgres`, `/usr/lib/postgresql/*/bin/pg_*`)
  - Kill any process not in the allow-list (`sigkill` action)
  - Note: kagent is pure Go — no shell, no git, no external tools. The engine calls MCP tools over HTTP, it doesn't execute them locally.

- **File access enforcement** (`kprobe` on `open/openat`):
  - Read-only: `/usr`, `/lib`, `/etc`, `/proc`, `/dev/urandom`
  - Read-write: PostgreSQL data dir (`/var/lib/postgresql/`), `/tmp`, `/dev/null`, `/run`
  - Deny: everything else

- **No network enforcement** — Cilium handles L3/L4

**5b.3** Update `apps/wol-proxy/allow-ingress-from-open-webui.yaml` → extend to also allow ingress from `agentgateway-system` namespace on port 11434 (kagent engine routes LLM traffic via AgentGateway → wol-proxy)

### Phase 6: ProductHub Integration

**6.1** ProductHub talks to **kagent API** instead of creating raw K8s Jobs:
- Send coding task as a conversation to kagent agent session (REST)
- Message format: `"Implement the following task on repo <url>, branch <name>: <description>"`
- kagent agent processes asynchronously
- ProductHub polls session status for completion

**6.2** ProductHub MCP Server (optional) — runs in `mcp` namespace:
- Registered as kagent `MCPServer` CRD (kagent manages lifecycle)
- OR as `AgentgatewayBackend` in AgentGateway
- Tools: `get_task`, `update_task_status`, `post_comment`, `get_prd`
- Allows coding agent to query ProductHub for task context during execution

### Phase 7: WoL Integration

**7.1** Before sending a task, ProductHub calls wol-proxy health endpoint to ensure GPU node is awake
- If GPU node is down, wol-proxy wakes it (existing functionality)
- ProductHub waits for Ollama to be ready before creating session

**7.2** Fallback: kagent engine retries Ollama connection with exponential backoff

## Key CRDs

| CRD                   | Source                    | Purpose                                                     |
| --------------------- | ------------------------- | ----------------------------------------------------------- |
| `Agent`               | kagent                    | Declarative agent definition (prompt, tools, skills, model) |
| `ModelConfig`         | kagent                    | LLM provider config (Ollama host, model name)               |
| `MCPServer`           | kagent/kmcp               | MCP tool server lifecycle management                        |
| `RemoteMCPServer`     | kagent                    | Ref to built-in kagent tool servers                         |
| `AgentgatewayBackend` | AgentGateway              | LLM/MCP backend routing target                              |
| `AgentgatewayPolicy`  | AgentGateway              | Token budgets, rate limits                                  |
| `TracingPolicy`       | Tetragon                  | eBPF process/file enforcement                               |
| `CiliumNetworkPolicy` | Cilium (existing)         | L3/L4 network isolation                                     |
| `Cluster`             | CloudNative-PG (existing) | PostgreSQL instances                                        |

## Files to Create

### Tetragon (Phase 1a)
- `apps/tetragon/kustomization.yaml`
- `apps/_argocd/tetragon-app.yaml`

### AgentGateway (Phase 1b)
- `apps/agentgateway/kustomization.yaml`
- `apps/agentgateway/gateway.yaml`
- `apps/agentgateway/ollama-backend.yaml`
- `apps/agentgateway/ollama-route.yaml`
- `apps/agentgateway/coding-tools-mcp-backend.yaml`
- `apps/agentgateway/coding-tools-mcp-route.yaml`
- `apps/_argocd/agentgateway-crds-app.yaml`
- `apps/_argocd/agentgateway-app.yaml`

### kagent (Phase 2)
- `apps/agents/kustomization.yaml`
- `apps/agents/postgres.yaml`
- `apps/agents/external-secret.yaml`
- `apps/agents/model-configs.yaml`
- `apps/agents/coding-agent.yaml`
- `apps/_argocd/kagent-crds-app.yaml`
- `apps/_argocd/kagent-app.yaml`

### Coding Tools MCP Server (Phase 3)
- `agents/coding-tools-mcp/main.go`
- `agents/coding-tools-mcp/Dockerfile`
- `agents/coding-tools-mcp/skills/pr-workflow.md`
- `agents/coding-tools-mcp/skills/code-review.md`
- `agents/coding-tools-mcp/skills/task-decomposition.md`
- `apps/mcp/namespace.yaml`
- `apps/mcp/deployment.yaml`
- `apps/mcp/service.yaml`
- `apps/mcp/external-secret.yaml`
- `apps/mcp/kustomization.yaml`
- `apps/_argocd/mcp-app.yaml`

### Sandboxing (Phase 5)
- `apps/mcp/cilium-network-policy.yaml`
- `apps/mcp/tetragon-policy.yaml`
- `apps/agents/cilium-network-policy.yaml`
- `apps/agents/tetragon-policy.yaml`

### Files to Modify
- `apps/_argocd/kustomization.yaml` — add tetragon, agentgateway-crds, agentgateway, kagent-crds, kagent, mcp entries (alphabetical)
- `apps/wol-proxy/allow-ingress-from-open-webui.yaml` — add `agentgateway-system` namespace
- `apps/open-webui/configmap.yaml` — update `OLLAMA_BASE_URL` to point at AgentGateway (Phase 1b.7)

## Decisions

- **kagent replaces raw K8s Jobs** — declarative Agent CRDs instead of ephemeral Jobs; kagent controller manages agent pod lifecycle
- **Single LLM loop, full local workspace** — kagent engine drives coding directly via a custom MCP tool server with file CRUD, regex code search, shell execution, git, GitHub CLI, and web fetching. No wrapper layers, no double LLM overhead. The LLM reasons and calls tools in one loop.
- **Custom coding-tools-mcp over existing MCP servers** — Existing reference servers (Filesystem, Git, GitHub, Fetch) each run in separate pods and can't share a local workspace filesystem. A single custom Go server provides all capabilities in one pod with a shared `/workspace` volume. Avoids coordinating 4+ separate services.
- **Go ADK runtime** — ~2s startup vs ~15s for Python, lower resource usage; sufficient for coding tasks
- **All LLM traffic via AgentGateway** — kagent, Open-WebUI, and ProductHub all route through AgentGateway (→ wol-proxy → Ollama on GPU host). Unified token tracking, rate limiting, and observability across all consumers.
- **ProductHub selects model per task** — Multiple ModelConfig CRDs (e.g. `ollama-nano`, `ollama-large`). ProductHub invokes the appropriate agent variant based on task complexity. New models added by creating a new ModelConfig + Agent CRD pair.
- **Single-task execution only** — One agent session at a time. No concurrency. GPU can't parallelize inference effectively. Keep it simple.
- **No agent memory (no pgvector)** — kagent postgres stores conversations only. Long-term memory via pgvector skipped for now. Can be added later.
- **Go built-in search, no ripgrep** — `search_files` uses Go's `regexp` + `filepath.Walk`. No external `rg` binary. Simpler container, simpler Tetragon policy, sufficient for homelab-scale repos.
- **All HTTPS egress allowed** — CiliumNetworkPolicy permits all port 443 egress for `fetch_url` to reach arbitrary documentation. RFC1918 denied to prevent lateral movement.
- **Tetragon for process + file** — process allow-listing (git, gh, bash, sh, jq, coding-tools-mcp) and file access control (r/w only in /workspace and /tmp); no network rules (delegated to Cilium + AgentGateway). No ripgrep or curl binaries — search and HTTP are Go-native.
- **Skip agentregistry** — GitOps is sufficient governance at homelab scale; can add later if agent/skill catalog grows
- **Sandboxing on both namespaces** — `mcp` namespace: restrictive (process allow-list, file access control, all HTTPS egress, RFC1918 denied). `agents` namespace: infrastructure-grade (network restricted to AgentGateway + mcp + K8s API + postgres, process restricted to Go binaries + postgres, no internet egress). Defense in depth — even if kagent is compromised, lateral movement is limited.
- **kmcp for MCP server** — integrated with kagent, native Go, lower footprint vs Python FastMCP
- **Coding tools are pure side-effects** — MCP server never calls the LLM; it only executes file/git/shell/fetch operations. All reasoning stays in kagent engine.
- **Open-WebUI migrated to AgentGateway** — `OLLAMA_BASE_URL` updated to point at AgentGateway in Phase 1b. Unifies all LLM consumers under one observability layer.

## Open Questions

_All original open questions have been resolved and moved to Decisions above._

## Resolved Questions (for reference)

1. **kagent Ollama via AgentGateway** → **Resolved: route through AgentGateway.** Path: kagent → AgentGateway → wol-proxy → Ollama on GPU host. AgentGateway provides unified observability for all consumers.

2. **Concurrent coding tasks** → **Resolved: single-task only.** One agent session at a time. GPU can't parallelize inference effectively on a single Ollama instance. Keep it simple.

3. **Open-WebUI migration** → **Resolved: yes, migrate now (Phase 1b.7).** Update `OLLAMA_BASE_URL` to point at AgentGateway. All LLM consumers (kagent, Open-WebUI, ProductHub) route through AgentGateway for unified token tracking.

4. **kagent database with pgvector** → **Resolved: skip agent memory for now.** CloudNative-PG for kagent conversation store only, no `pgvector` extension. Memory can be added later when needed.

5. **Model selection** → **Resolved: ProductHub decides per task.** Multiple ModelConfig CRDs (e.g. `ollama-nano`, `ollama-large`). ProductHub selects which agent to invoke based on task complexity. AgentGateway routes to the right Ollama model backend.

6. **fetch_url egress scope** → **Resolved: allow all HTTPS egress.** Simpler CiliumNetworkPolicy — all port 443 traffic permitted. RFC1918 still denied to prevent lateral movement.

7. **ripgrep vs built-in search** → **Resolved: Go built-in `regexp` + `filepath.Walk`.** No external `rg` binary dependency. Simpler container image, simpler Tetragon policy. Sufficient performance for homelab-scale repos.
