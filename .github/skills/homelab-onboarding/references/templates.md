# File Templates

All templates use `<placeholder>` syntax for values gathered during the interview phase.
Replace all placeholders with actual values. Remove any optional sections that don't apply.

## Table of Contents

1. [namespace.yaml](#namespaceyaml)
2. [deployment.yaml](#deploymentyaml)
3. [service.yaml](#serviceyaml)
4. [configmap.yaml](#configmapyaml)
5. [external-secret.yaml](#external-secretyaml)
6. [postgres.yaml](#postgresyaml)
7. [pvc.yaml](#pvcyaml)
8. [cronjob.yaml](#cronjobyaml)
9. [kustomization.yaml (app)](#kustomizationyaml-app)
10. [ArgoCD Application](#argocd-application)
11. [HTTPRoute — Home](#httproute--home)
12. [HTTPRoute — Internet](#httproute--internet)
13. [Dockerfile — Python/FastAPI](#dockerfile--pythonfastapi)
14. [Dockerfile — React/Vite + nginx](#dockerfile--reactvite--nginx)
15. [GitHub Actions CI/CD](#github-actions-cicd)
16. [docker-compose.yml](#docker-composeyml)

---

## namespace.yaml

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: <app-name>
  # Add labels below ONLY for internet-facing apps:
  # labels:
  #   exposure: internet
```

For internet-facing apps, uncomment and include the `exposure: internet` label.

---

## deployment.yaml

Single-service deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <app-name>
  namespace: <app-name>
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: <app-name>
  template:
    metadata:
      labels:
        app: <app-name>
    spec:
      containers:
        - name: <app-name>
          image: <image>
          ports:
            - containerPort: <port>
          envFrom:
            - configMapRef:
                name: <app-name>-config
            - secretRef:
                name: <app-name>-secrets
          livenessProbe:
            httpGet:
              path: /health
              port: <port>
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: <port>
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

Multi-service deployment (e.g., backend + frontend): create a separate Deployment for each
service, naming them `<app-name>-backend` and `<app-name>-frontend`. Each gets its own labels,
ports, probes, and resources. Use `<app-name>-<service>` for deployment and label names.

If the deployment uses a PVC, add under `spec.template.spec`:

```yaml
      volumes:
        - name: <app-name>-data
          persistentVolumeClaim:
            claimName: <app-name>-data
```

And under the container:

```yaml
          volumeMounts:
            - name: <app-name>-data
              mountPath: <mount-path>
```

Remove `envFrom` entries that don't apply (e.g., no `secretRef` if no secrets, no `configMapRef`
if no configmap).

---

## service.yaml

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
      targetPort: <port>
```

For multi-service apps, create one Service per service that needs to be reachable. The main
service (the one the HTTPRoute points to) should be named `<app-name>`. Internal-only services
can be named `<app-name>-<service>`.

---

## configmap.yaml

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: <app-name>-config
  namespace: <app-name>
data:
  TZ: "Europe/Berlin"
  # Add app-specific env vars here
```

---

## external-secret.yaml

Basic app secrets:

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
    - secretKey: <ENV_VAR_NAME>
      remoteRef:
        key: <app-name>-<secret-key-kebab>
```

PostgreSQL credentials (separate ExternalSecret, only if database is needed):

```yaml
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: <app-name>-postgres-credentials
  namespace: <app-name>
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: <app-name>-postgres-credentials
    template:
      type: kubernetes.io/basic-auth
      data:
        username: "app"
        password: "{{ .password }}"
    creationPolicy: Owner
  data:
    - secretKey: password
      remoteRef:
        key: <app-name>-postgres-password
```

Templated DATABASE_URL (combine with app secrets if needed):

```yaml
  target:
    name: <app-name>-secrets
    template:
      type: Opaque
      data:
        DATABASE_URL: "postgresql://app:{{ .password }}@<app-name>-postgres-rw:5432/<db-name>"
    creationPolicy: Owner
  data:
    - secretKey: password
      remoteRef:
        key: <app-name>-postgres-password
```

Note: CloudNative-PG exposes services as `<cluster-name>-rw` for the read-write endpoint.

---

## postgres.yaml

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
      database: <db-name>
      owner: app
      secret:
        name: <app-name>-postgres-credentials
  storage:
    size: <db-storage-size>
    storageClassName: hcloud-volumes
```

---

## pvc.yaml

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app-name>-data
  namespace: <app-name>
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: <storage-class>
  resources:
    requests:
      storage: <storage-size>
```

---

## cronjob.yaml

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: <app-name>-<job-name>
  namespace: <app-name>
spec:
  schedule: "<cron-expression>"
  timeZone: "Europe/Berlin"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: <job-name>
              image: curlimages/curl
              command: ["curl", "-X", "POST", "http://<target-service>:<port>/<endpoint>"]
          restartPolicy: OnFailure
```

---

## kustomization.yaml (app)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - configmap.yaml
  - external-secret.yaml
  - postgres.yaml
  - pvc.yaml
  - deployment.yaml
  - service.yaml
  - cronjob.yaml
```

Only list files that actually exist. Order: namespace → config → secrets → database → storage →
deployment → service → cronjob.

---

## ArgoCD Application

File: `apps/_argocd/<app-name>-app.yaml`

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

---

## HTTPRoute — Home

File: `networking/httproutes/home/<app-name>.yaml`

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
        - name: <service-name>
          port: 80
```

---

## HTTPRoute — Internet

File: `networking/httproutes/internet/<app-name>.yaml`

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
        - name: <service-name>
          port: 80
```

---

## Dockerfile — Python/FastAPI

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
```

## Dockerfile — Node.js

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY . .
CMD ["node", "index.js"]
```

## Dockerfile — React/Vite + nginx

```dockerfile
FROM node:18-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

---

## GitHub Actions CI/CD

File: `.github/workflows/build-and-push-images.yml`

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
        service: [<service-list>]
        include:
          - service: <service-name>
            dockerfile: ./<service-name>/Dockerfile
            context: ./<service-name>
            image_name: <service-name>

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
          platforms: linux/amd64,linux/arm64
```

For single-service apps, remove the `strategy.matrix` and hardcode the service values directly.

---

## docker-compose.yml

```yaml
services:
  <service-name>:
    build:
      context: ./<service-dir>
      dockerfile: Dockerfile
    ports:
      - "<host-port>:<container-port>"
    environment:
      - TZ=Europe/Berlin
    depends_on:
      - postgres  # if database needed

  # Include only if database is needed:
  postgres:
    image: postgres:17.2
    environment:
      POSTGRES_DB: <db-name>
      POSTGRES_USER: app
      POSTGRES_PASSWORD: dev-password
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data

volumes:
  postgres-data:
```
