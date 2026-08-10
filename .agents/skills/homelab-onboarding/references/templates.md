# File Templates

All templates use `<placeholder>` syntax for values gathered during the interview phase.
Replace all placeholders with actual values. Remove any optional sections that don't apply.

## Table of Contents

1. [namespace.yaml](#namespaceyaml)
2. [deployment.yaml](#deploymentyaml)
3. [service.yaml](#serviceyaml)
4. [configmap.yaml](#configmapyaml)
5. [external-secret.yaml](#external-secretyaml)
6. [appdb.yaml](#appdbyaml)
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
          # If the app needs a database, add individual env entries for DB credentials:
          # env:
          #   - name: <DB_USERNAME_ENV_VAR>
          #     valueFrom:
          #       secretKeyRef:
          #         name: <app-name>-db-connection
          #         key: username
          #   - name: <DB_PASSWORD_ENV_VAR>
          #     valueFrom:
          #       secretKeyRef:
          #         name: <app-name>-db-connection
          #         key: password
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
  # If the app needs a database, add the central cluster host:
  # <DB_HOST_ENV_VAR>: "central-postgres-rw.postgres.svc.cluster.local"
  # <DB_PORT_ENV_VAR>: "5432"
  # <DB_NAME_ENV_VAR>: "<db-name>"
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

Note: PostgreSQL credentials are **NOT** managed via ExternalSecret for new apps. They are
automatically provisioned by Crossplane via the AppDBClaim, which creates a
`<app-name>-db-connection` secret with keys: `host`, `port`, `username`, `password`, `dbname`,
`sslmode`, `uri`.

If an app uses a `DATABASE_URL` env var, reference the Crossplane connection secret's `uri` key
in the Deployment:

```yaml
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: <app-name>-db-connection
        key: uri
```

If the app needs a non-standard URI scheme (e.g., `postgresql+asyncpg://`), use an ExternalSecret
template that reads from the Crossplane connection secret instead of Azure Key Vault.

---

## appdb.yaml

Crossplane AppDBClaim for provisioning a database on the central shared CNPG cluster:

```yaml
apiVersion: k8s.homelab.timosur.com/v1
kind: AppDBClaim
metadata:
  name: <app-name>-db
  namespace: <app-name>
spec:
  appName: <app-name>
  databaseName: <db-name>
  roleName: <role-name>
  compositionRef:
    name: appdb-central-postgres
  # Only if the app needs PostgreSQL extensions:
  # extensions:
  #   - name: vector
```

Naming rules:
- `appName` **must** match the app's namespace (Crossplane creates the connection secret there)
- `databaseName`: the PostgreSQL database name (default: `app`, or use the app name)
- `roleName`: the PostgreSQL role/user name (default: same as app name)
- Use underscores in role/database names if needed (e.g., `vinyl_manager`)

Crossplane automatically creates a secret named `<appName>-db-connection` in the app's namespace
with keys: `host`, `port`, `username`, `password`, `dbname`, `sslmode`, `uri`.

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
  - appdb.yaml
  - pvc.yaml
  - deployment.yaml
  - service.yaml
  - cronjob.yaml
```

Only list files that actually exist. Order: namespace → config → secrets → database (appdb) →
storage → deployment → service → cronjob.

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
