# Copilot Instructions

## Agent Behavior

### Workflow

Always follow this sequence: **Plan → Research → Implement → Verify**. Before any significant action, reason through the problem first. Decompose work into phases with atomic, self-contained tasks — each task should be a complete execution recipe with clear acceptance criteria.

### Core Principles

- **Empirical rigor**: Never assume or act on unverified information. All conclusions and decisions must be based on verified facts — read the actual files, check existing patterns, confirm state with tools. Do not hallucinate file contents or configurations.
- **Autonomous execution**: Prefer autonomous resolution and tool use over asking the user. Only ask when essential input is genuinely unobtainable through available tools, or a single question would prevent excessive effort.
- **Appropriate complexity**: Use minimum necessary complexity for a robust, correct, and maintainable solution. Balance YAGNI/KISS with genuinely required robustness — no gold-plating, no under-engineering. Earmark ideas for optional enhancements separately rather than implementing unrequested features.
- **Consistency**: Before creating anything new, search the codebase for pre-existing patterns, reusable components, and established conventions. Match them exactly.
- **Cleanliness**: When changes make existing code/config obsolete, remove it immediately. No dead code, no orphaned resources, no backward compatibility unless explicitly requested.
- **Change awareness**: Consider the impact of every change — security implications, performance effects, and whether signature changes need propagation to upstream/downstream consumers.
- **Security by default**: Proactively consider common vulnerabilities — validate inputs, protect secrets, use secure defaults. Never store secrets in plain text.
- **Resilience**: Implement necessary error handling and boundary checks. Solutions should be robust, not fragile.
- **Adaptability**: If the planned approach hits unforeseen obstacles, change strategy rather than repeatedly retrying the same failing action.

### Design Heuristics

Apply SOLID principles where appropriate: single responsibility for functions/classes, open-closed for extensibility, dependency inversion over tight coupling.

## Project Overview

K3s homelab cluster managed via GitOps. All infrastructure changes happen through Git — ArgoCD syncs this repo to the cluster automatically. The cluster has 1 AMD control plane node and 2 ARM worker nodes, so multi-arch compatibility matters.

## Architecture

**GitOps flow**: Git push → ArgoCD detects changes → syncs to K3s cluster.

**App of Apps pattern**: `apps/root.yaml` points to `apps/_argocd/`, which contains ArgoCD Application CRDs that each point to an app's manifests in `apps/<app-name>/`. All app manifests use Kustomize.

**Two ArgoCD app types**:

- **Kustomize apps** (most apps): ArgoCD Application points to `apps/<app-name>/` containing raw K8s manifests with a `kustomization.yaml`
- **Helm + overlay apps** (cert-manager, envoy-gateway): ArgoCD Application uses `sources:` with a Helm chart plus a local overlay path from this repo

**Networking is dual-gateway**: Two separate Envoy Gateway instances segment home (LAN) and internet traffic. Home services use `*.home.timosur.com` on HTTP; internet services use `*.timosur.com` on HTTPS with cert-manager + Let's Encrypt DNS-01 via Cloudflare.

**Secrets**: All secrets live in Azure Key Vault and are synced via External Secrets Operator using `ClusterSecretStore` named `azure-keyvault-store`.

**Storage**: Two storage classes — `homelab-iscsi` (Synology iSCSI via `csi.san.synology.com`) and `homelab-smb` (SMB via `smb.csi.k8s.io`, default).

**Databases**: PostgreSQL via CloudNative-PG operator. Each app gets its own `Cluster` CRD in `postgres.yaml`.

## Key Conventions

### Adding a new application

Follow `ONBOARDING_GUIDE.md` for the complete checklist. The critical steps:

1. Create `apps/<app-name>/` with Kustomize manifests (namespace.yaml, deployment.yaml, service.yaml, kustomization.yaml at minimum)
2. Create `apps/_argocd/<app-name>-app.yaml` and add it to `apps/_argocd/kustomization.yaml`
3. Create HTTPRoute in `networking/httproutes/home/<app-name>.yaml` or `networking/httproutes/internet/<app-name>.yaml` and register in the corresponding `kustomization.yaml`
4. If internet-facing, label namespace with `exposure=internet`

### Manifest conventions

- Pin container images with SHA digest: `image: ghcr.io/timosur/<repo>/<service>:sha-<commit>@sha256:<digest>`
- Each app gets its own namespace (same name as the app)
- Use `strategy.type: Recreate` for single-replica deployments with PVCs
- Always include `livenessProbe` and `readinessProbe`
- Set resource `requests` and `limits` on all containers
- ConfigMaps for non-secret env vars, ExternalSecrets for secrets
- Timezone is `Europe/Berlin` in ConfigMaps

### Networking conventions

- Home HTTPRoutes reference gateway `envoy-gateway-home` in namespace `envoy-gateway-system`
- Internet HTTPRoutes reference gateway `envoy-gateway-internet` in namespace `envoy-gateway-internet-system` with `sectionName: https`
- A cluster-wide `default-deny-ingress` Cilium policy blocks all ingress by default; traffic from envoy gateway namespaces is already allowed
- Custom domains (not `*.timosur.com`) need dedicated listeners and certificates added to `networking/gateways/internet/gateway.yaml`

### ExternalSecret pattern

```yaml
secretStoreRef:
  name: azure-keyvault-store
  kind: ClusterSecretStore
```

Azure Key Vault key naming: `<app-name>-<secret-key>` (e.g., `garden-database-password`). For PostgreSQL credentials, use `kubernetes.io/basic-auth` type with templated secrets.

### ArgoCD Application template

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <app-name>
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/timosur/homelab.git
    targetRevision: HEAD
    path: apps/<app-name>
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

## Infrastructure Provisioning

Ansible playbooks in `ansible/` manage cluster provisioning. Run from the control plane node:

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/k3s-cluster.yml          # Full cluster setup
ansible-playbook -i inventory.yml playbooks/argocd-gitops-setup.yml   # Bootstrap ArgoCD + GitOps
```

## Renovate

Renovate Bot auto-updates container images and Helm chart versions. It runs weekly (Monday before 6 AM), pins digests for `ghcr.io/timosur/*` images, and auto-merges digest updates. Configuration is in `renovate.json`. It ignores `keys/`, markdown files, and `kustomization.yaml` files.
