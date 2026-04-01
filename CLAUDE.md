# CLAUDE.md

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

**Storage**: Two storage classes — `homelab-iscsi` (Synology iSCSI via `csi.san.synology.com`) and `homelab-smb` (SMB via `smb.csi.k8s.io`, default). Default for PVCs is `homelab-smb`.

**Databases**: PostgreSQL via CloudNative-PG operator. Each app gets its own `Cluster` CRD in `postgres.yaml`.

## Adding a New Application

Follow `ONBOARDING_GUIDE.md` for the complete checklist. Critical steps:

1. Create `apps/<app-name>/` with Kustomize manifests (namespace.yaml, deployment.yaml, service.yaml, kustomization.yaml at minimum)
2. Create `apps/_argocd/<app-name>-app.yaml` and add it to `apps/_argocd/kustomization.yaml`
3. Create HTTPRoute in `networking/httproutes/home/<app-name>.yaml` or `networking/httproutes/internet/<app-name>.yaml` and register in the corresponding `kustomization.yaml`
4. If internet-facing, label namespace with `exposure=internet`

## Manifest Conventions

- Pin container images with SHA digest: `image: ghcr.io/timosur/<repo>/<service>:sha-<commit>@sha256:<digest>`
- Each app gets its own namespace (same name as the app)
- Use `strategy.type: Recreate` for single-replica deployments with PVCs
- Always include `livenessProbe` and `readinessProbe`
- Set resource `requests` and `limits` on all containers
- ConfigMaps for non-secret env vars, ExternalSecrets for secrets
- Timezone is `Europe/Berlin` in ConfigMaps
- Before creating anything new, search the codebase for pre-existing patterns and match them exactly

## Networking Conventions

- Home HTTPRoutes reference gateway `envoy-gateway-home` in namespace `envoy-gateway-system`
- Internet HTTPRoutes reference gateway `envoy-gateway-internet` in namespace `envoy-gateway-internet-system` with `sectionName: https`
- A cluster-wide `default-deny-ingress` Cilium policy blocks all ingress by default; traffic from envoy gateway namespaces is already allowed
- Custom domains (not `*.timosur.com`) need dedicated listeners and certificates added to `networking/gateways/internet/gateway.yaml`

## ExternalSecret Pattern

```yaml
secretStoreRef:
  name: azure-keyvault-store
  kind: ClusterSecretStore
```

Azure Key Vault key naming: `<app-name>-<secret-key>` (e.g., `garden-database-password`). For PostgreSQL credentials, use `kubernetes.io/basic-auth` type with templated secrets.

## ArgoCD Application Template

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
