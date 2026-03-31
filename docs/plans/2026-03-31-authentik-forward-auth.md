# Authentik Forward-Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a new Authentik instance at `auth.timosur.com` and protect the 4 WOL-proxy internet HTTPRoutes with Envoy Gateway SecurityPolicy ExtAuth, preventing bots from waking the desktop node.

**Architecture:** A standalone Authentik deployment (server, worker, redis, postgres) exposes its embedded outpost at `auth.timosur.com`. Envoy Gateway SecurityPolicy resources target each WOL-proxy HTTPRoute and delegate auth decisions to Authentik's `/outpost.goauthentik.io/auth/envoy` endpoint. Unauthenticated requests get redirected to the Authentik login page.

**Tech Stack:** Authentik 2026.2.1, Envoy Gateway 1.7.1 (SecurityPolicy + ExtAuth), Crossplane AppDBClaim, ExternalSecrets (Azure Key Vault), Kustomize, ArgoCD

**Reference spec:** `docs/specs/2026-03-31-authentik-forward-auth-design.md`

---

### Task 1: Create Authentik namespace and database

**Files:**
- Create: `apps/authentik/namespace.yaml`
- Create: `apps/authentik/appdb.yaml`

- [ ] **Step 1: Create namespace.yaml**

```yaml
# apps/authentik/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: authentik
  labels:
    exposure: internet
```

- [ ] **Step 2: Create appdb.yaml**

Note: The existing `bike-weather-auth` already uses `databaseName: authentik` and `roleName: authentik`. This new instance must use different names to avoid conflicts on the shared central-postgres cluster.

```yaml
# apps/authentik/appdb.yaml
apiVersion: k8s.homelab.timosur.com/v1
kind: AppDBClaim
metadata:
  name: authentik-db
  namespace: authentik
spec:
  appName: authentik
  databaseName: authentik_main
  roleName: authentik_main
  compositionRef:
    name: appdb-central-postgres
```

- [ ] **Step 3: Commit**

```bash
git add apps/authentik/namespace.yaml apps/authentik/appdb.yaml
git commit -m "feat(authentik): add namespace and database claim"
```

---

### Task 2: Create Authentik secrets and configuration

**Files:**
- Create: `apps/authentik/external-secret.yaml`
- Create: `apps/authentik/configmap.yaml`

Prerequisites: The following keys must exist in Azure Key Vault (`homelab-timosur`):
- `authentik-secret-key` — random string for Authentik encryption (generate with `openssl rand -hex 32`)

- [ ] **Step 1: Create external-secret.yaml**

```yaml
# apps/authentik/external-secret.yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: authentik-secrets
  namespace: authentik
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: authentik-secrets
    creationPolicy: Owner
  data:
    - secretKey: AUTHENTIK_SECRET_KEY
      remoteRef:
        key: authentik-secret-key
```

- [ ] **Step 2: Create configmap.yaml**

```yaml
# apps/authentik/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: authentik-config
  namespace: authentik
data:
  TZ: "Europe/Berlin"
  AUTHENTIK_REDIS__HOST: "authentik-redis"
  AUTHENTIK_POSTGRESQL__HOST: "central-postgres-rw.postgres.svc.cluster.local"
  AUTHENTIK_POSTGRESQL__PORT: "5432"
  AUTHENTIK_POSTGRESQL__NAME: "authentik_main"
```

- [ ] **Step 3: Commit**

```bash
git add apps/authentik/external-secret.yaml apps/authentik/configmap.yaml
git commit -m "feat(authentik): add secrets and configuration"
```

---

### Task 3: Create Redis deployment

**Files:**
- Create: `apps/authentik/redis-deployment.yaml`
- Create: `apps/authentik/redis-service.yaml`

- [ ] **Step 1: Create redis-deployment.yaml**

```yaml
# apps/authentik/redis-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: authentik-redis
  namespace: authentik
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: authentik-redis
  template:
    metadata:
      labels:
        app: authentik-redis
    spec:
      containers:
        - name: redis
          image: redis:8-alpine
          ports:
            - containerPort: 6379
          livenessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            exec:
              command: ["redis-cli", "ping"]
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "256Mi"
              cpu: "250m"
```

- [ ] **Step 2: Create redis-service.yaml**

```yaml
# apps/authentik/redis-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: authentik-redis
  namespace: authentik
spec:
  selector:
    app: authentik-redis
  ports:
    - name: redis
      port: 6379
      targetPort: 6379
  type: ClusterIP
```

- [ ] **Step 3: Commit**

```bash
git add apps/authentik/redis-deployment.yaml apps/authentik/redis-service.yaml
git commit -m "feat(authentik): add redis deployment and service"
```

---

### Task 4: Create Authentik server deployment

**Files:**
- Create: `apps/authentik/server-deployment.yaml`
- Create: `apps/authentik/server-service.yaml`
- Create: `apps/authentik/media-pvc.yaml`

- [ ] **Step 1: Create media-pvc.yaml**

```yaml
# apps/authentik/media-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: authentik-media
  namespace: authentik
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: hcloud-volumes
  resources:
    requests:
      storage: 5Gi
```

- [ ] **Step 2: Create server-deployment.yaml**

Runs on `homelab-amd` (always-on control plane) to avoid chicken-and-egg with WOL.

```yaml
# apps/authentik/server-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: authentik-server
  namespace: authentik
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: authentik-server
  template:
    metadata:
      labels:
        app: authentik-server
    spec:
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
      containers:
        - name: server
          image: ghcr.io/goauthentik/server:2026.2.1
          command: ["ak", "server"]
          ports:
            - containerPort: 9000
          envFrom:
            - configMapRef:
                name: authentik-config
            - secretRef:
                name: authentik-secrets
          env:
            - name: AUTHENTIK_POSTGRESQL__USER
              valueFrom:
                secretKeyRef:
                  name: authentik-db-connection
                  key: username
            - name: AUTHENTIK_POSTGRESQL__PASSWORD
              valueFrom:
                secretKeyRef:
                  name: authentik-db-connection
                  key: password
          volumeMounts:
            - name: media
              mountPath: /media
          livenessProbe:
            httpGet:
              path: /-/health/live/
              port: 9000
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /-/health/ready/
              port: 9000
            initialDelaySeconds: 15
            periodSeconds: 10
          resources:
            requests:
              memory: "256Mi"
              cpu: "200m"
            limits:
              memory: "1Gi"
              cpu: "1000m"
      volumes:
        - name: media
          persistentVolumeClaim:
            claimName: authentik-media
```

- [ ] **Step 3: Create server-service.yaml**

```yaml
# apps/authentik/server-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: authentik-server
  namespace: authentik
spec:
  selector:
    app: authentik-server
  ports:
    - name: http
      port: 9000
      targetPort: 9000
  type: ClusterIP
```

- [ ] **Step 4: Commit**

```bash
git add apps/authentik/media-pvc.yaml apps/authentik/server-deployment.yaml apps/authentik/server-service.yaml
git commit -m "feat(authentik): add server deployment, service, and media PVC"
```

---

### Task 5: Create Authentik worker deployment

**Files:**
- Create: `apps/authentik/worker-deployment.yaml`

- [ ] **Step 1: Create worker-deployment.yaml**

```yaml
# apps/authentik/worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: authentik-worker
  namespace: authentik
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: authentik-worker
  template:
    metadata:
      labels:
        app: authentik-worker
    spec:
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
      containers:
        - name: worker
          image: ghcr.io/goauthentik/server:2026.2.1
          command: ["ak", "worker"]
          envFrom:
            - configMapRef:
                name: authentik-config
            - secretRef:
                name: authentik-secrets
          env:
            - name: AUTHENTIK_POSTGRESQL__USER
              valueFrom:
                secretKeyRef:
                  name: authentik-db-connection
                  key: username
            - name: AUTHENTIK_POSTGRESQL__PASSWORD
              valueFrom:
                secretKeyRef:
                  name: authentik-db-connection
                  key: password
          volumeMounts:
            - name: media
              mountPath: /media
          resources:
            requests:
              memory: "256Mi"
              cpu: "200m"
            limits:
              memory: "1Gi"
              cpu: "1000m"
      volumes:
        - name: media
          persistentVolumeClaim:
            claimName: authentik-media
```

- [ ] **Step 2: Commit**

```bash
git add apps/authentik/worker-deployment.yaml
git commit -m "feat(authentik): add worker deployment"
```

---

### Task 6: Create Kustomization and ArgoCD application

**Files:**
- Create: `apps/authentik/kustomization.yaml`
- Create: `apps/_argocd/authentik-app.yaml`
- Modify: `apps/_argocd/kustomization.yaml`

- [ ] **Step 1: Create apps/authentik/kustomization.yaml**

```yaml
# apps/authentik/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: authentik

resources:
  - namespace.yaml
  - configmap.yaml
  - external-secret.yaml
  - appdb.yaml
  - redis-deployment.yaml
  - redis-service.yaml
  - server-deployment.yaml
  - server-service.yaml
  - worker-deployment.yaml
  - media-pvc.yaml
```

- [ ] **Step 2: Create apps/_argocd/authentik-app.yaml**

```yaml
# apps/_argocd/authentik-app.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: authentik
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/timosur/homelab.git
    targetRevision: HEAD
    path: apps/authentik
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
```

- [ ] **Step 3: Add authentik-app.yaml to ArgoCD kustomization**

In `apps/_argocd/kustomization.yaml`, add `authentik-app.yaml` to the resources list (alphabetically, after `amd-gpu-device-plugin-app.yaml`):

```yaml
resources:
  - agentgateway-crds-app.yaml
  - agentgateway-app.yaml
  - amd-gpu-device-plugin-app.yaml
  - authentik-app.yaml                  # ADD THIS LINE
  - bike-weather-app.yaml
  # ... rest unchanged
```

- [ ] **Step 4: Validate kustomization builds**

```bash
cd apps/authentik && kustomize build . && cd ../..
```

Expected: Valid YAML output with all resources, no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/authentik/kustomization.yaml apps/_argocd/authentik-app.yaml apps/_argocd/kustomization.yaml
git commit -m "feat(authentik): add kustomization and ArgoCD application"
```

---

### Task 7: Create HTTPRoute for auth.timosur.com

**Files:**
- Create: `networking/httproutes/internet/authentik.yaml`
- Modify: `networking/httproutes/internet/kustomization.yaml`

Note: The existing wildcard listener `https` on `*.timosur.com` already covers `auth.timosur.com`. No gateway changes needed.

- [ ] **Step 1: Create authentik.yaml HTTPRoute**

```yaml
# networking/httproutes/internet/authentik.yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: authentik
  namespace: authentik
spec:
  parentRefs:
    - name: envoy-gateway-internet
      namespace: envoy-gateway-internet-system
      sectionName: https
  hostnames:
    - "auth.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: authentik-server
          port: 9000
```

- [ ] **Step 2: Add to kustomization**

In `networking/httproutes/internet/kustomization.yaml`, add `authentik.yaml` to the resources list (alphabetically, before `actual.yaml`):

```yaml
resources:
  - actual.yaml
  - authentik.yaml                      # ADD THIS LINE
  - bike-weather.yaml
  # ... rest unchanged
```

- [ ] **Step 3: Commit**

```bash
git add networking/httproutes/internet/authentik.yaml networking/httproutes/internet/kustomization.yaml
git commit -m "feat(authentik): add HTTPRoute for auth.timosur.com"
```

---

### Task 8: Create SecurityPolicy resources for WOL-proxy routes

**Files:**
- Create: `networking/security-policies/paperless-auth.yaml`
- Create: `networking/security-policies/actual-auth.yaml`
- Create: `networking/security-policies/n8n-auth.yaml`
- Create: `networking/security-policies/mealie-auth.yaml`
- Create: `networking/security-policies/kustomization.yaml`
- Modify: `networking/kustomization.yaml`

SecurityPolicy must be in the same namespace as the targeted HTTPRoute. Each of the 4 WOL-proxy HTTPRoutes is in its respective service namespace.

- [ ] **Step 1: Create paperless-auth.yaml**

```yaml
# networking/security-policies/paperless-auth.yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: wol-auth-paperless
  namespace: paperless
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: paperless
  extAuth:
    http:
      backendRef:
        name: authentik-server
        namespace: authentik
        port: 9000
      path: /outpost.goauthentik.io/auth/envoy
      headersToBackend:
        - cookie
        - authorization
```

- [ ] **Step 2: Create actual-auth.yaml**

```yaml
# networking/security-policies/actual-auth.yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: wol-auth-actual
  namespace: actual
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: actual
  extAuth:
    http:
      backendRef:
        name: authentik-server
        namespace: authentik
        port: 9000
      path: /outpost.goauthentik.io/auth/envoy
      headersToBackend:
        - cookie
        - authorization
```

- [ ] **Step 3: Create n8n-auth.yaml**

```yaml
# networking/security-policies/n8n-auth.yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: wol-auth-n8n
  namespace: n8n
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: n8n
  extAuth:
    http:
      backendRef:
        name: authentik-server
        namespace: authentik
        port: 9000
      path: /outpost.goauthentik.io/auth/envoy
      headersToBackend:
        - cookie
        - authorization
```

- [ ] **Step 4: Create mealie-auth.yaml**

```yaml
# networking/security-policies/mealie-auth.yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: wol-auth-mealie
  namespace: mealie
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mealie
  extAuth:
    http:
      backendRef:
        name: authentik-server
        namespace: authentik
        port: 9000
      path: /outpost.goauthentik.io/auth/envoy
      headersToBackend:
        - cookie
        - authorization
```

- [ ] **Step 5: Create kustomization.yaml for security-policies**

```yaml
# networking/security-policies/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - actual-auth.yaml
  - mealie-auth.yaml
  - n8n-auth.yaml
  - paperless-auth.yaml
```

- [ ] **Step 6: Add security-policies to networking kustomization**

In `networking/kustomization.yaml`, add `security-policies`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - cilium-lb-ipam
  - cilium-network-policies
  - gateways
  - httproutes
  - security-policies
```

- [ ] **Step 7: Validate kustomization builds**

```bash
cd networking && kustomize build . && cd ..
```

Expected: Valid YAML output including all 4 SecurityPolicy resources, no errors.

- [ ] **Step 8: Commit**

```bash
git add networking/security-policies/ networking/kustomization.yaml
git commit -m "feat(authentik): add SecurityPolicy ExtAuth for WOL-proxy routes"
```

---

### Task 9: Add Azure Key Vault secret

**Files:** None (Azure CLI operation)

This task must be done before deploying, otherwise the ExternalSecret will fail to sync.

- [ ] **Step 1: Generate and store the Authentik secret key**

```bash
SECRET_KEY=$(openssl rand -hex 32)
az keyvault secret set \
  --vault-name homelab-timosur \
  --name authentik-secret-key \
  --value "$SECRET_KEY"
```

Expected: JSON output confirming the secret was created.

- [ ] **Step 2: Verify the secret exists**

```bash
az keyvault secret show \
  --vault-name homelab-timosur \
  --name authentik-secret-key \
  --query "name" -o tsv
```

Expected: `authentik-secret-key`

---

### Task 10: Deploy and verify

**Files:** None (git push + cluster verification)

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Wait for ArgoCD sync and verify Authentik pods**

```bash
kubectl get pods -n authentik
```

Expected: 3 pods running — `authentik-server-*`, `authentik-worker-*`, `authentik-redis-*`

- [ ] **Step 3: Verify Authentik server is healthy**

```bash
kubectl logs -n authentik -l app=authentik-server --tail=20
```

Expected: Logs showing server startup, no errors.

- [ ] **Step 4: Verify auth.timosur.com is reachable**

```bash
curl -s -o /dev/null -w "%{http_code}" https://auth.timosur.com/-/health/live/
```

Expected: `200`

- [ ] **Step 5: Verify SecurityPolicies are applied**

```bash
kubectl get securitypolicies -A
```

Expected: 4 SecurityPolicy resources in namespaces `paperless`, `actual`, `n8n`, `mealie`.

- [ ] **Step 6: Test auth redirect works**

```bash
curl -s -o /dev/null -w "%{http_code}" https://docs.timosur.com/
```

Expected: `302` or `401` (redirect to Authentik login, not direct pass-through).

---

### Task 11: Configure Authentik via admin UI

**Files:** None (manual UI configuration)

After Authentik is running, configure it via the admin UI. On first access, Authentik will prompt to create the initial admin account.

- [ ] **Step 1: Create admin account**

Navigate to `https://auth.timosur.com/if/flow/initial-setup/` and create the admin user.

- [ ] **Step 2: Create Proxy Provider**

In Admin UI → Applications → Providers → Create:
- Name: `wol-proxy-forward-auth`
- Type: Proxy Provider
- Authorization flow: `default-provider-authorization-implicit-consent`
- Mode: **Forward auth (single application)**
- External host: `https://auth.timosur.com`

- [ ] **Step 3: Create Application**

In Admin UI → Applications → Applications → Create:
- Name: `WOL Proxy Services`
- Slug: `wol-proxy-services`
- Provider: `wol-proxy-forward-auth`
- Launch URL: (leave blank)

- [ ] **Step 4: Configure Embedded Outpost**

In Admin UI → Applications → Outposts:
- Edit the `authentik Embedded Outpost`
- Add the `WOL Proxy Services` application to its application list
- Save

- [ ] **Step 5: End-to-end test**

1. Open `https://docs.timosur.com/` in a browser (incognito)
2. Verify redirect to Authentik login page
3. Log in with the admin account
4. Verify redirect back to `docs.timosur.com` with the page loading (desktop wakes up)
5. Open `https://finance.timosur.com/` — verify it loads without re-login (SSO)
6. Verify `https://docs.home.timosur.com/` still works without auth (home network unaffected)
