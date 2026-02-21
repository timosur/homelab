# Creating a New App for the Homelab

This guide covers everything needed to deploy a new application in the homelab infrastructure — from setting up the app repository to registering it in the homelab repo.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [App Repository Setup](#app-repository-setup)
3. [Homelab Manifests](#homelab-manifests)
4. [Networking & Ingress](#networking--ingress)
5. [Secrets Management](#secrets-management)
6. [Database Setup](#database-setup)
7. [Storage](#storage)
8. [Image Updates & Renovate](#image-updates--renovate)
9. [Registering the App in ArgoCD](#registering-the-app-in-argocd)
10. [Common Patterns](#common-patterns)
11. [Security Best Practices](#security-best-practices)
12. [Checklist](#checklist)

---

## Architecture Overview

```
App Repo (GitHub)
  └─ GitHub Actions builds Docker images → ghcr.io/timosur/<app>/<service>

Homelab Repo (GitHub)
  ├─ apps/<app-name>/          ← Kubernetes manifests (Kustomize)
  ├─ apps/_argocd/             ← ArgoCD Application CRDs
  └─ networking/httproutes/    ← HTTPRoute definitions

ArgoCD (App of Apps)
  └─ root.yaml → _argocd/ kustomization → <app>-app.yaml → apps/<app-name>/
```

**Deployment flow:**
1. Push code to app repo → GitHub Actions builds & pushes multi-arch Docker images to GHCR
2. Update image SHA in `apps/<app-name>/*-deployment.yaml` in homelab repo
3. ArgoCD detects changes and syncs → Kubernetes deploys updated pods

---

## App Repository Setup

### Repository Structure

```
<app-name>/
├── .github/
│   └── workflows/
│       └── build-and-push-images.yml    # CI/CD pipeline
├── backend/                              # Backend service (if applicable)
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── ...
├── frontend/                             # Frontend service (if applicable)
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
├── docker-compose.yml                    # Local development
└── README.md
```

### Dockerfiles

Every service that runs in K8s needs its own Dockerfile. Follow these conventions:

**Backend (Python/FastAPI):**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
```

**Frontend (React/Vite → nginx):**
```dockerfile
# Build stage
FROM node:18-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### GitHub Actions CI/CD

Create `.github/workflows/build-and-push-images.yml`:

```yaml
name: Build and Push Docker Images

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    strategy:
      matrix:
        service: [backend, frontend]  # Add all services here
        include:
          - service: backend
            dockerfile: ./backend/Dockerfile
            context: ./backend
            image_name: backend
          - service: frontend
            dockerfile: ./frontend/Dockerfile
            context: ./frontend
            image_name: frontend

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/${{ matrix.image_name }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=sha,prefix=sha-
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: ${{ matrix.context }}
          file: ${{ matrix.dockerfile }}
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          platforms: linux/amd64,linux/arm64  # Multi-arch for ARM nodes
```

**Key points:**
- Images are pushed to `ghcr.io/timosur/<repo-name>/<service>`
- Always build **multi-platform** (`linux/amd64,linux/arm64`) for compatibility
- Use `sha-<commit>` tags for pinned deployments in K8s

### Local Development

Provide a `docker-compose.yml` for local development with all services, matching the K8s architecture as closely as possible.

### Health Checks

Every service should expose a health endpoint:
- Backend: `GET /health` returning `200 OK`
- Frontend: nginx serves static files (health = responds on `/`)

---

## Homelab Manifests

All manifests live under `apps/<app-name>/` using Kustomize.

### Required Files

```
apps/<app-name>/
├── kustomization.yaml        # Lists all resources
├── namespace.yaml             # Namespace definition
├── deployment.yaml            # Deployment(s)
└── service.yaml               # Service(s)
```

### Optional Files (as needed)

```
├── configmap.yaml             # Non-secret environment variables
├── external-secret.yaml       # Secrets from Azure Key Vault
├── secret.yaml                # Secrets derived from ExternalSecret data
├── pvc.yaml                   # Persistent volume claims
├── postgres.yaml              # CloudNative-PG database cluster
├── storage-class.yaml         # Custom storage class (if needed)
├── cronjob.yaml               # Scheduled tasks
```

### kustomization.yaml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - configmap.yaml
  - external-secret.yaml
  - secret.yaml
  - pvc.yaml
  - postgres.yaml
  - deployment.yaml
  - service.yaml
```

### namespace.yaml

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: <app-name>
```

### deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <app-name>-backend
  namespace: <app-name>
spec:
  replicas: 1
  strategy:
    type: Recreate           # Use Recreate for single-replica with PVC
  selector:
    matchLabels:
      app: <app-name>-backend
  template:
    metadata:
      labels:
        app: <app-name>-backend
    spec:
      containers:
        - name: backend
          image: ghcr.io/timosur/<repo-name>/backend:sha-<commit>@sha256:<digest>
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: <app-name>-config
            - secretRef:
                name: <app-name>-secrets
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "500m"
```

**Important:**
- Pin images with SHA digest: `image: ghcr.io/.../backend:sha-abc123@sha256:...`
- Use `strategy.type: Recreate` for single-replica deployments with PVCs
- Add `livenessProbe` and `readinessProbe`

### service.yaml

```yaml
apiVersion: v1
kind: Service
metadata:
  name: <app-name>
  namespace: <app-name>
spec:
  selector:
    app: <app-name>
  ports:
    - port: 80
      targetPort: 8000
```

### configmap.yaml

For non-secret environment variables:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: <app-name>-config
  namespace: <app-name>
data:
  TZ: "Europe/Berlin"
  BASE_URL: "https://<app-name>.home.timosur.com"
  # Add more non-secret config here
```

---

## Networking & Ingress

The cluster uses **Envoy Gateway** (Gateway API) with **cert-manager** for TLS (DNS-01 via Cloudflare). DNS is managed externally — the Unifi Dream Router (UDR) runs a DynDNS client that keeps Cloudflare A records for `*.timosur.com`, `timosur.com`, and `givgroov.de` up to date. Home services use `*.home.timosur.com` resolved via local DNS (e.g. Pi-hole).

There are two gateways:
- **envoy-gateway-home** (`*.home.timosur.com`, HTTP only, LAN access) — for internal/home services
- **envoy-gateway-internet** (`*.timosur.com`, `timosur.com`, `givgroov.de`, HTTPS, internet-facing) — for publicly exposed services

> **Important:** A cluster-wide `default-deny-ingress` Cilium policy blocks all ingress to non-system namespaces by default. New apps receive traffic from the envoy gateway namespaces automatically (allowed in the policy), but be aware that inter-namespace communication requires explicit `CiliumNetworkPolicy` rules.

### Home Services (LAN only, `*.home.timosur.com`)

Most apps use the home gateway. No per-app gateway listener is needed — the wildcard listener handles all `*.home.timosur.com` hostnames.

#### Create HTTPRoute

Create `networking/httproutes/home/<app-name>.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: <app-name>-home
  namespace: <app-name>
spec:
  parentRefs:
    - name: envoy-gateway-home
      namespace: envoy-gateway-system
  hostnames:
    - "<app-name>.home.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: <app-name>
          port: 80
```

#### Register HTTPRoute in Kustomization

Add the route to `networking/httproutes/home/kustomization.yaml`:

```yaml
resources:
  - <app-name>.yaml
```

### Internet-Facing Services (`*.timosur.com`)

For apps that need public access, use the internet gateway. TLS is handled via a wildcard certificate and DNS-01 challenge via Cloudflare.

If the app uses a custom domain (not `*.timosur.com`), add dedicated HTTP + HTTPS listeners to `networking/gateways/internet/gateway.yaml` with a separate certificate.

#### Create HTTPRoute

Create `networking/httproutes/internet/<app-name>.yaml`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: <app-name>
  namespace: <app-name>
spec:
  parentRefs:
    - name: envoy-gateway-internet
      namespace: envoy-gateway-internet-system
      sectionName: https
  hostnames:
    - "<app-name>.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: <app-name>
          port: 80
```

#### Register HTTPRoute in Kustomization

Add the route to `networking/httproutes/internet/kustomization.yaml`:

```yaml
resources:
  - <app-name>.yaml
```

#### Label Namespace for Network Policy

Internet-facing namespaces must be labeled so the Cilium egress policy restricts LAN access:

```bash
kubectl label ns <app-name> exposure=internet
```

---

## Secrets Management

All secrets are stored in **Azure Key Vault** and synced to Kubernetes via **External Secrets Operator**.

### 1. Add Secrets to Azure Key Vault

Store secrets in Azure Key Vault (`homelab-timosur`). Use a naming convention like `<app-name>-<secret-key>` (e.g., `myapp-database-password`).

### 2. Create ExternalSecret

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: <app-name>-secrets
  namespace: <app-name>
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: <app-name>-secrets
    creationPolicy: Owner
  data:
    - secretKey: API_KEY
      remoteRef:
        key: <app-name>-api-key
    - secretKey: DATABASE_PASSWORD
      remoteRef:
        key: <app-name>-database-password
```

### Templated Secrets

For secrets that need value interpolation (e.g., constructing a `DATABASE_URL`):

```yaml
spec:
  target:
    name: <app-name>-secrets
    template:
      type: Opaque
      data:
        DATABASE_URL: "postgresql://app:{{ .password }}@<app-name>-postgres:5432/app"
  data:
    - secretKey: password
      remoteRef:
        key: <app-name>-database-password
```

### Basic Auth Secrets (for postgres)

```yaml
spec:
  target:
    name: <app-name>-postgres-credentials
    template:
      type: kubernetes.io/basic-auth
      data:
        username: "app"
        password: "{{ .password }}"
  data:
    - secretKey: password
      remoteRef:
        key: <app-name>-postgres-password
```

---

## Database Setup

Use **CloudNative-PG** for PostgreSQL databases.

### postgres.yaml

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: <app-name>-postgres
  namespace: <app-name>
spec:
  instances: 1
  imageName: postgres:17.2
  bootstrap:
    initdb:
      database: app
      owner: app
      secret:
        name: <app-name>-postgres-credentials
  storage:
    size: 10Gi
    storageClassName: hcloud-volumes
```

This requires:
- An `ExternalSecret` creating `<app-name>-postgres-credentials` with `kubernetes.io/basic-auth` type
- A `Secret` with `POSTGRES_PASSWORD` for the app to use (or construct `DATABASE_URL` via template)

---

## Storage

### Available Storage Classes

| StorageClass | Provider | Use Case |
|---|---|---|
| `hcloud-volumes` | Synology CSI Driver (iSCSI) | Default, general purpose block storage |
| `storage-box-smb` | SMB CSI Driver | Large shared storage via NAS/SMB |

> Note: The storage class names `hcloud-volumes` and `storage-box-smb` are aliased from the Synology CSI driver for compatibility.

### PVC Example

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app-name>-data
  namespace: <app-name>
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: hcloud-volumes
  resources:
    requests:
      storage: 10Gi
```

### Custom Storage Class (if needed)

For specific mount options (e.g., uid/gid for SMB):

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: <app-name>-storage-box-smb
provisioner: smb.csi.k8s.io
parameters:
  source: //<storage-box-host>/<share>
  csi.storage.k8s.io/node-stage-secret-name: storage-box-smb-credentials
  csi.storage.k8s.io/node-stage-secret-namespace: storage-box-smb
mountOptions:
  - dir_mode=0777
  - file_mode=0777
  - uid=1000
  - gid=1000
reclaimPolicy: Retain
volumeBindingMode: Immediate
```

---

## Image Updates & Renovate

The homelab uses **Renovate Bot** to track and update container images automatically.

### How it works

- Renovate scans `apps/**/*.yaml` for Docker image references
- For GHCR images (`ghcr.io/timosur/*`), it pins digests and creates PRs on new pushes
- Digest updates are auto-merged
- Renovate runs weekly (Monday before 6 AM)

### Image reference format

Use this format in deployments for Renovate to track:

```yaml
image: ghcr.io/timosur/<repo-name>/<service>:sha-<commit>@sha256:<digest>
```

Renovate will automatically create PRs when new images are pushed to GHCR.

---

## Registering the App in ArgoCD

### 1. Create ArgoCD Application

Create `apps/_argocd/<app-name>-app.yaml`:

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

### 2. Register in Kustomization

Add the app to `apps/_argocd/kustomization.yaml`:

```yaml
resources:
  - <app-name>-app.yaml
```

---

## Common Patterns

### Volume Mounts with Subdirectories

When an app needs multiple directories on the same volume:

```yaml
volumeMounts:
  - name: app-data
    mountPath: /app/uploads
    subPath: uploads
  - name: app-data
    mountPath: /app/config
    subPath: config
```

### Init Containers for Directory Setup

```yaml
initContainers:
  - name: init-directories
    image: busybox:1.36
    command: ["sh", "-c", "mkdir -p /app/uploads /app/config && chown -R 1000:1000 /app"]
    volumeMounts:
      - name: app-data
        mountPath: /app
```

### Reverse Proxy with Basic Auth

For apps that need authentication via nginx in front of the actual services (see `apps/garden/` for a full example):

- Deploy an `nginx:alpine` container alongside the app
- Use an init container to generate `htpasswd` from secrets
- Route traffic: `/` → frontend, `/api/` → backend
- Expose the nginx service as the app's entry point

### CronJobs

For scheduled tasks (e.g., daily API calls):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: <app-name>-scheduler
  namespace: <app-name>
spec:
  schedule: "0 1 * * *"        # Daily at 01:00
  timeZone: "Europe/Berlin"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: scheduler
              image: curlimages/curl
              command: ["curl", "-X", "POST", "http://<app-name>-backend:8000/api/trigger"]
          restartPolicy: OnFailure
```

---

## Security Best Practices

- Never store passwords or secrets in plain text — use External Secrets for all sensitive data
- Use specific image tags with SHA digest, never `:latest` in production manifests
- Set appropriate resource `requests` and `limits`
- Add `livenessProbe` and `readinessProbe` to all containers
- Run containers as non-root when possible (`securityContext.runAsNonRoot: true`)
- Use `fsGroup` in `securityContext` when containers need to write to mounted volumes

---

## Checklist

### App Repository

- [ ] Each service has a `Dockerfile` with multi-arch support
- [ ] GitHub Actions workflow builds & pushes to GHCR (`linux/amd64` + `linux/arm64`)
- [ ] Images tagged with `sha-<commit>` and `latest`
- [ ] Health check endpoint(s) implemented
- [ ] `docker-compose.yml` for local development
- [ ] Environment config via env vars (12-factor style)

### Homelab Repository — Pre-deployment

- [ ] `apps/<app-name>/` directory with Kustomize manifests
  - [ ] `kustomization.yaml`
  - [ ] `namespace.yaml`
  - [ ] `deployment.yaml` (with probes, SHA-pinned images)
  - [ ] `service.yaml`
  - [ ] `configmap.yaml` (if non-secret env vars needed)
  - [ ] `external-secret.yaml` (if secrets needed)
  - [ ] `postgres.yaml` (if database needed)
  - [ ] `pvc.yaml` (if persistent storage needed)
- [ ] ArgoCD Application registered in `apps/_argocd/<app-name>-app.yaml`
- [ ] ArgoCD kustomization updated in `apps/_argocd/kustomization.yaml`
- [ ] Secrets added to Azure Key Vault
- [ ] HTTPRoute created in `networking/httproutes/home/<app-name>.yaml` or `networking/httproutes/internet/<app-name>.yaml`
- [ ] HTTPRoute registered in the corresponding `kustomization.yaml`
- [ ] Namespace labeled `exposure=internet` (if internet-facing)

### Post-deployment Verification

- [ ] ArgoCD shows application as synced and healthy
- [ ] Pods are running and ready
- [ ] Service is accessible via URL
- [ ] TLS certificate is issued (internet-facing apps only)
- [ ] Database connection working (if applicable)
- [ ] Renovate detects the new GHCR images
