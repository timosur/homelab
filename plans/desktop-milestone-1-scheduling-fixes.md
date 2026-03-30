# Milestone 1: Immediate Scheduling Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix mis-scheduled workloads — pin critical services to homelab-amd, exclude incompatible apps from ARM, and assign lightweight static sites to ARM nodes.

**Architecture:** Pure scheduling changes via `nodeSelector` and `nodeAffinity` on existing deployments and Helm values. No new infrastructure, no code changes. Each task is an independent, atomic fix that can be committed and deployed separately.

**Tech Stack:** K3s, Kustomize, CNPG, ArgoCD (Helm values)

**Dependencies:** None — this milestone can start immediately.

**Parallelism:** All 6 tasks are independent and can run in parallel.

---

## Target Node Distribution (this milestone only)

| Change                | Current Node        | Target Node      |
| --------------------- | ------------------- | ---------------- |
| bike-weather-postgres | homelab-arm-large   | homelab-amd      |
| open-webui redis      | homelab-arm-large   | homelab-amd      |
| coding-tools-mcp      | ARM (failing)       | homelab-amd      |
| tetragon DaemonSet    | all nodes incl. ARM | amd64 nodes only |
| kagent                | any (incl. ARM)     | amd64 nodes only |
| givgroov, portfolio   | any                 | arm64 nodes      |

---

## Files Modified

- `apps/bike-weather/postgres.yaml` — Add `affinity.nodeSelector` for homelab-amd
- `apps/open-webui/redis-deployment.yaml` — Add `nodeSelector` for homelab-amd
- `apps/mcp/deployment.yaml` — Add `nodeSelector` for homelab-amd
- `apps/_argocd/tetragon-app.yaml` — Add ARM node affinity exclusion
- `apps/_argocd/kagent-app.yaml` — Add affinity to exclude ARM
- `apps/givgroov/deployment.yaml` — Add `nodeSelector` for arm64
- `apps/portfolio/deployment.yaml` — Add `nodeSelector` for arm64

---

### Task 1: Pin bike-weather-postgres to homelab-amd

The CNPG Cluster for bike-weather has no affinity and landed on crash-prone `homelab-arm-large`.

**Files:**
- Modify: `apps/bike-weather/postgres.yaml`

- [ ] **Step 1: Add nodeSelector affinity to CNPG Cluster**

Add `affinity.nodeSelector` to pin postgres to the control plane (matching the pattern used in `apps/open-webui/postgres.yaml`):

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: bike-weather-postgres
  namespace: bike-weather
spec:
  instances: 1
  enablePDB: false

  affinity:
    nodeSelector:
      kubernetes.io/hostname: homelab-amd

  postgresql:
    parameters:
      max_connections: "200"
      shared_buffers: "256MB"
      effective_cache_size: "512MB"

  bootstrap:
    initdb:
      database: app
      owner: app
      secret:
        name: bike-weather-postgres-credentials

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

- [ ] **Step 2: Verify**

```bash
git diff apps/bike-weather/postgres.yaml
kubectl apply -f apps/bike-weather/postgres.yaml --dry-run=client
```

Wait for ArgoCD sync, then verify:
```bash
kubectl get pods -n bike-weather -l cnpg.io/cluster=bike-weather-postgres -o wide
```
Expected: Pod migrates to `homelab-amd`.

- [ ] **Step 3: Commit**

```bash
git add apps/bike-weather/postgres.yaml
git commit -m "fix: pin bike-weather-postgres to homelab-amd"
```

---

### Task 2: Pin open-webui redis to homelab-amd

Redis is on `homelab-arm-large` while all other open-webui pods are on `homelab-amd`. The `local-path` PVC is bound to arm-large so it must be deleted and recreated. Redis is a cache — data loss is acceptable.

**Files:**
- Modify: `apps/open-webui/redis-deployment.yaml`

- [ ] **Step 1: Add nodeSelector to redis deployment**

Add `nodeSelector` to the pod spec, after `spec.template.spec`:

```yaml
    spec:
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
      containers:
        - name: redis
```

The full updated file:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: open-webui
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
      containers:
        - name: redis
          image: redis:8-alpine
          ports:
            - containerPort: 6379
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "256Mi"
              cpu: "200m"
          volumeMounts:
            - name: redis-data
              mountPath: /data
            - name: redis-config
              mountPath: /usr/local/etc/redis
          command:
            - redis-server
            - /usr/local/etc/redis/redis.conf
          readinessProbe:
            exec:
              command:
                - redis-cli
                - ping
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            exec:
              command:
                - redis-cli
                - ping
            initialDelaySeconds: 30
            periodSeconds: 15
      volumes:
        - name: redis-data
          persistentVolumeClaim:
            claimName: redis-pvc
        - name: redis-config
          configMap:
            name: redis-config
```

- [ ] **Step 2: Delete the old PVC bound to arm-large**

```bash
kubectl delete pvc redis-pvc -n open-webui
```

The new pod will trigger PVC recreation on `homelab-amd` via `local-path`.

- [ ] **Step 3: Verify**

```bash
kubectl get pods -n open-webui -l app=redis -o wide
kubectl get pvc -n open-webui redis-pvc
```
Expected: Redis running on `homelab-amd`, new PVC bound to `homelab-amd`.

- [ ] **Step 4: Commit**

```bash
git add apps/open-webui/redis-deployment.yaml
git commit -m "fix: pin open-webui redis to homelab-amd"
```

---

### Task 3: Pin coding-tools-mcp to homelab-amd

The MCP image is amd64-only (`ghcr.io/timosur/homelab/coding-tools-mcp:latest`). It's in `ImagePullBackOff` on ARM due to 403 (likely no ARM variant). Pin it to amd64.

**Files:**
- Modify: `apps/mcp/deployment.yaml`

- [ ] **Step 1: Add nodeSelector to MCP deployment**

Add `nodeSelector` after `spec.template.spec`:

```yaml
    spec:
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
      containers:
        - name: coding-tools-mcp
```

- [ ] **Step 2: Verify**

```bash
kubectl get pods -n mcp -o wide
```
Expected: Pod rescheduling on `homelab-amd`. Note: the 403 error is a separate issue (image auth), but at least it won't fail on ARM.

- [ ] **Step 3: Commit**

```bash
git add apps/mcp/deployment.yaml
git commit -m "fix: pin coding-tools-mcp to homelab-amd (amd64-only)"
```

---

### Task 4: Exclude tetragon from ARM nodes

Tetragon is CrashLooping on both ARM nodes — kernel `6.12.75+rpt` lacks BTF support. Tetragon is deployed via Helm; add an `affinity` to exclude ARM nodes.

**Files:**
- Modify: `apps/_argocd/tetragon-app.yaml`

- [ ] **Step 1: Add affinity to tetragon Helm values**

Add `tetragon.affinity` to the Helm valuesObject to exclude ARM nodes:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: tetragon
  namespace: argocd
spec:
  project: default
  sources:
    - repoURL: https://helm.cilium.io
      chart: tetragon
      targetRevision: v1.6.0
      helm:
        valuesObject:
          tetragon:
            enabled: true
            daemonSetOverrides:
              affinity:
                nodeAffinity:
                  requiredDuringSchedulingIgnoredDuringExecution:
                    nodeSelectorTerms:
                      - matchExpressions:
                          - key: kubernetes.io/arch
                            operator: NotIn
                            values:
                              - arm64
          tetragonOperator:
            enabled: true
    - repoURL: https://github.com/timosur/homelab.git
      targetRevision: HEAD
      path: apps/tetragon
  destination:
    server: https://kubernetes.default.svc
    namespace: kube-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

Note: The Tetragon Helm chart uses `tetragon.daemonSetOverrides.affinity` to set DaemonSet affinity. If this key doesn't work, the alternative is `tetragon.affinity`. Check the chart values with:

```bash
helm show values cilium/tetragon --version v1.6.0 | grep -A5 affinity
```

- [ ] **Step 2: Verify**

After ArgoCD sync:
```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=tetragon -o wide
```
Expected: Tetragon pods only on `homelab-amd` and `homelab-gpu`, none on ARM nodes.

- [ ] **Step 3: Commit**

```bash
git add apps/_argocd/tetragon-app.yaml
git commit -m "fix: exclude tetragon from ARM nodes (no BTF support)"
```

---

### Task 5: Exclude kagent/agents from ARM nodes

Kagent is deployed via Helm. Add affinity to prevent scheduling on ARM.

**Files:**
- Modify: `apps/_argocd/kagent-app.yaml`

- [ ] **Step 1: Add affinity to kagent Helm values**

Add controller and UI affinity to exclude ARM:

```yaml
          controller:
            affinity:
              nodeAffinity:
                requiredDuringSchedulingIgnoredDuringExecution:
                  nodeSelectorTerms:
                    - matchExpressions:
                        - key: kubernetes.io/arch
                          operator: NotIn
                          values:
                            - arm64
            volumes:
              - name: db-connection
                secret:
                  secretName: agents-db-connection
            volumeMounts:
              - name: db-connection
                mountPath: /etc/kagent/db
                readOnly: true
```

Also add to any other pod specs in kagent that might schedule (UI, engine). Check the chart:

```bash
helm show values oci://ghcr.io/kagent-dev/kagent/helm/kagent --version 0.8.0 2>/dev/null | grep -B2 -A5 affinity
```

- [ ] **Step 2: Verify**

```bash
kubectl get pods -n agents -o wide
```
Expected: All kagent pods on amd64 nodes only.

- [ ] **Step 3: Commit**

```bash
git add apps/_argocd/kagent-app.yaml
git commit -m "fix: exclude kagent from ARM nodes"
```

---

### Task 6: Pin givgroov and portfolio to ARM

These are lightweight static sites perfect for ARM.

**Files:**
- Modify: `apps/givgroov/deployment.yaml`
- Modify: `apps/portfolio/deployment.yaml`

- [ ] **Step 1: Add nodeSelector to givgroov**

Add `nodeSelector` to prefer ARM:

```yaml
    spec:
      nodeSelector:
        kubernetes.io/arch: arm64
      containers:
        - name: givgroov
```

- [ ] **Step 2: Add nodeSelector to portfolio**

```yaml
    spec:
      nodeSelector:
        kubernetes.io/arch: arm64
      containers:
        - name: portfolio
```

- [ ] **Step 3: Verify both images support ARM**

```bash
# Check if images have arm64 manifests
kubectl get deploy -n givgroov givgroov -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl get deploy -n portfolio portfolio -o jsonpath='{.spec.template.spec.containers[0].image}'
# Both should already be running on ARM (givgroov is on homelab-arm-small currently)
```

- [ ] **Step 4: Commit**

```bash
git add apps/givgroov/deployment.yaml apps/portfolio/deployment.yaml
git commit -m "feat: pin givgroov and portfolio to ARM nodes"
```
