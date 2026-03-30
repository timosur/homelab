# Milestone 4: App Migration & Verification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all desktop-tier apps (paperless, actual, mealie, vinyl-manager, n8n, bike-weather-preview) to `homelab-amd-desktop` with proper `nodeSelector` and toleration, then verify the entire integration end-to-end.

**Architecture:** Each app deployment gets `nodeSelector: kubernetes.io/hostname: homelab-amd-desktop` and a toleration for the `availability=daytime:NoSchedule` taint. Apps with `local-path` PVCs bound to other nodes (paperless) need PVC recreation with `homelab-smb`. CNPG postgres instances remain on `homelab-amd` — app pods connect via cluster DNS. HTTPRoutes already point to wol-proxy (from Milestone 3).

**Tech Stack:** K3s, Kustomize

**Dependencies:**
- **Milestone 2 must be complete** — desktop node must be provisioned and joined to cluster
- **Milestone 3 must be complete** — HTTPRoutes must point to wol-proxy before apps move, otherwise traffic goes nowhere when pods restart on a sleeping node

**Parallelism:** Tasks 19-24 (app migrations) are independent and can run in parallel. Task 25 (verification) must run last.

---

## Standard Toleration + NodeSelector Block

All desktop app deployments receive this block under `spec.template.spec`:

```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
```

---

## Target Workload Distribution (final state)

| Node                                | Role                              | Workloads                                                                                                                                                                              |
| ----------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **homelab-amd** (always-on)         | Control plane, critical services  | pi-hole, home-assistant, open-webui (all incl. redis), bike-weather (all incl. postgres), bike-weather-auth, wol-proxy, central-postgres, monitoring, agents/kagent, mcp, agentgateway |
| **homelab-amd-desktop** (on-demand) | Non-critical, woken via WoL proxy | paperless, actual, mealie (+mcp-server), vinyl-manager (all 3), n8n, bike-weather-preview                                                                                              |
| **homelab-arm-small/large**         | Lightweight only                  | givgroov, portfolio + system DaemonSets (cilium, kured, monitoring, synology-csi, smb-csi)                                                                                             |
| **homelab-gpu** (on-demand)         | GPU workloads                     | Ollama (via WoL proxy port-based, unchanged)                                                                                                                                           |

---

## File Structure

### Modified files
- `apps/paperless/pvc.yaml` — Change `local-path` → `homelab-smb`
- `apps/paperless/deployment.yaml` — Add nodeSelector + toleration for desktop
- `apps/actual/deployment.yaml` — Add nodeSelector + toleration for desktop
- `apps/mealie/deployment.yaml` — Change nodeSelector from homelab-amd → desktop + toleration
- `apps/mealie/mcp-server-deployment.yaml` — Change nodeSelector from homelab-amd → desktop + toleration
- `apps/vinyl-manager/backend-deployment.yaml` — Add nodeSelector + toleration
- `apps/vinyl-manager/frontend-deployment.yaml` — Add nodeSelector + toleration
- `apps/vinyl-manager/audio-analyzer-deployment.yaml` — Add nodeSelector + toleration
- `apps/n8n/deployment.yaml` — Change nodeSelector from homelab-amd → desktop + toleration
- `apps/bike-weather-preview/backend-deployment.yaml` — Add nodeSelector + toleration
- `apps/bike-weather-preview/frontend-deployment.yaml` — Add nodeSelector + toleration
- `apps/bike-weather-preview/nginx-deployment.yaml` — Add nodeSelector + toleration
- `apps/bike-weather-preview/agent-deployment.yaml` — Add nodeSelector + toleration

---

### Task 19: Migrate paperless to desktop

Paperless PVCs are `local-path` bound to `homelab-arm-large`. Since it's not in use, delete PVCs and change to `homelab-smb`.

**Files:**
- Modify: `apps/paperless/pvc.yaml`
- Modify: `apps/paperless/deployment.yaml`

- [ ] **Step 1: Delete existing paperless PVCs**

```bash
kubectl scale deployment paperless -n paperless --replicas=0
kubectl scale deployment paperless-redis -n paperless --replicas=0
kubectl delete pvc paperless-data paperless-media paperless-export paperless-consume -n paperless
```

- [ ] **Step 2: Change PVC storage class to homelab-smb**

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: paperless-data
  namespace: paperless
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: homelab-smb
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: paperless-media
  namespace: paperless
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 100Gi
  storageClassName: homelab-smb
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: paperless-export
  namespace: paperless
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 20Gi
  storageClassName: homelab-smb
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: paperless-consume
  namespace: paperless
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: homelab-smb
```

- [ ] **Step 3: Add nodeSelector + toleration to both deployments**

For the `paperless` deployment, add after `spec.template.spec`:

```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
```

Same for `paperless-redis` deployment.

- [ ] **Step 4: Verify**

```bash
kubectl get pods -n paperless -o wide
kubectl get pvc -n paperless
```
Expected: Both pods on `homelab-amd-desktop`, PVCs using `homelab-smb`.

- [ ] **Step 5: Commit**

```bash
git add apps/paperless/pvc.yaml apps/paperless/deployment.yaml
git commit -m "feat: migrate paperless to desktop node with homelab-smb"
```

---

### Task 20: Migrate actual to desktop

Actual uses `homelab-smb` — freely movable.

**Files:**
- Modify: `apps/actual/deployment.yaml`

- [ ] **Step 1: Add nodeSelector + toleration**

Add after `spec.template.spec`:

```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
      containers:
```

- [ ] **Step 2: Commit**

```bash
git add apps/actual/deployment.yaml
git commit -m "feat: migrate actual to desktop node"
```

---

### Task 21: Migrate mealie to desktop

Mealie has `homelab-smb` for data (movable) and `local-path` for postgres (bound to `homelab-amd`). The CNPG postgres stays on `homelab-amd` — mealie pods move to desktop and connect via cluster DNS.

**Files:**
- Modify: `apps/mealie/deployment.yaml`
- Modify: `apps/mealie/mcp-server-deployment.yaml`

- [ ] **Step 1: Update mealie deployment nodeSelector**

Change from:
```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
```
To:
```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
```

- [ ] **Step 2: Update mcp-server deployment nodeSelector**

Same change in `apps/mealie/mcp-server-deployment.yaml`.

- [ ] **Step 3: Commit**

```bash
git add apps/mealie/deployment.yaml apps/mealie/mcp-server-deployment.yaml
git commit -m "feat: migrate mealie to desktop node"
```

---

### Task 22: Migrate vinyl-manager to desktop

All 3 deployments have no nodeSelector. Postgres uses `homelab-smb` — freely movable.

**Files:**
- Modify: `apps/vinyl-manager/backend-deployment.yaml`
- Modify: `apps/vinyl-manager/frontend-deployment.yaml`
- Modify: `apps/vinyl-manager/audio-analyzer-deployment.yaml`

- [ ] **Step 1: Add nodeSelector + toleration to all three deployments**

Add after `spec.template.spec` in each file:

```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
      containers:
```

- [ ] **Step 2: Commit**

```bash
git add apps/vinyl-manager/backend-deployment.yaml apps/vinyl-manager/frontend-deployment.yaml apps/vinyl-manager/audio-analyzer-deployment.yaml
git commit -m "feat: migrate vinyl-manager to desktop node"
```

---

### Task 23: Migrate n8n to desktop

Currently has `nodeSelector: homelab-amd` and `replicas: 0`. Change nodeSelector.

**Files:**
- Modify: `apps/n8n/deployment.yaml`

- [ ] **Step 1: Update nodeSelector and add toleration**

Change from:
```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd
```
To:
```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
```

n8n uses `hcloud-volumes` PVC and AppDBClaim — both node-independent.

- [ ] **Step 2: Commit**

```bash
git add apps/n8n/deployment.yaml
git commit -m "feat: migrate n8n to desktop node"
```

---

### Task 24: Migrate bike-weather-preview to desktop

All deployments already have `replicas: 0`. Add nodeSelector + toleration for when they're scaled up.

**Files:**
- Modify: `apps/bike-weather-preview/backend-deployment.yaml`
- Modify: `apps/bike-weather-preview/frontend-deployment.yaml`
- Modify: `apps/bike-weather-preview/nginx-deployment.yaml`
- Modify: `apps/bike-weather-preview/agent-deployment.yaml`

- [ ] **Step 1: Add nodeSelector + toleration to all four deployments**

Add after `spec.template.spec` in each file:

```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
```

Note: The postgres CNPG Cluster in bike-weather-preview should stay on `homelab-amd` — add `affinity.nodeSelector` like Task 1 if not already present.

- [ ] **Step 2: Commit**

```bash
git add apps/bike-weather-preview/backend-deployment.yaml apps/bike-weather-preview/frontend-deployment.yaml apps/bike-weather-preview/nginx-deployment.yaml apps/bike-weather-preview/agent-deployment.yaml
git commit -m "feat: migrate bike-weather-preview to desktop node"
```

---

### Task 25: End-to-end verification

- [ ] **Step 1: Verify wol-proxy serves wake-up page**

With desktop node suspended:
```bash
curl -s -H "Host: docs.home.timosur.com" http://$(kubectl get svc desktop-proxy -n wol-proxy -o jsonpath='{.spec.clusterIP}'):8080 | head -20
```
Expected: HTML with "Desktop-Node wird hochgefahren" and JS polling script.

- [ ] **Step 2: Verify wol-proxy status endpoint**

```bash
curl -s -H "Host: docs.home.timosur.com" http://$(kubectl get svc desktop-proxy -n wol-proxy -o jsonpath='{.spec.clusterIP}'):8080/wol-proxy/status
```
Expected: `{"state": "sleeping", "node": "desktop"}` (or `"waking"` if wake was triggered)

- [ ] **Step 3: Verify wake and proxy flow**

Open `https://docs.timosur.com` in browser (or home equivalent). Expected flow:
1. Wake-up page appears with spinner
2. Status updates every 3 seconds
3. After node boots (~60-120s), page auto-redirects
4. Paperless loads normally

- [ ] **Step 4: Verify node distribution**

```bash
kubectl get pods -A -o wide | awk '{print $1, $2, $8}' | sort -k3
```

Check that:
- `homelab-amd`: pi-hole, home-assistant, open-webui (all), bike-weather (all incl. postgres), bike-weather-auth, wol-proxy, central-postgres, monitoring, agents, mcp
- `homelab-amd-desktop`: paperless (both), actual, mealie (+mcp-server), vinyl-manager (all 3), n8n (if scaled up), bike-weather-preview (if scaled up)
- `homelab-arm-small/large`: givgroov, portfolio + DaemonSets only
- `homelab-gpu`: kured DaemonSet only (Ollama is outside K8s)

- [ ] **Step 5: Verify no CrashLoopBackOff**

```bash
kubectl get pods -A --field-selector status.phase!=Running,status.phase!=Succeeded | grep -v Completed
```

- [ ] **Step 6: Verify tetragon excluded from ARM**

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=tetragon -o wide
```

Expected: No pods on `homelab-arm-small` or `homelab-arm-large`.

- [ ] **Step 7: Verify idle suspend**

Wait 30 min with no traffic to desktop apps, then:
```bash
kubectl logs -n wol-proxy -l app=wol-proxy --tail=20 | grep -i "suspend\|idle"
```
Expected: Logs show idle timeout reached and suspend command sent.

```bash
kubectl get node homelab-amd-desktop
```
Expected: Node goes `NotReady` after suspend.

- [ ] **Step 8: Commit all remaining changes and push**

```bash
git add -A
git commit -m "feat: complete desktop node integration and workload redistribution"
git push
```
