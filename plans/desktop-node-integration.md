# Desktop Node Integration & Workload Redistribution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `homelab-amd-desktop` as a daytime-only (8-22h) worker node, fix mis-scheduled workloads on ARM, redistribute apps across four node tiers.

**Architecture:** New desktop node joins the cluster with a `availability=daytime:NoSchedule` taint. Apps that tolerate downtime outside 8-22h are hard-pinned there. A CronJob on the control plane wakes the node via WoL at 07:55 and suspends it at 22:05 via SSH. ARM nodes are restricted to lightweight static sites only. Immediate fixes pin bike-weather-postgres and open-webui redis to the control plane, exclude tetragon from ARM, and fix MCP scheduling.

**Tech Stack:** K3s, Ansible, Kustomize, CNPG, CronJob, WoL, Cilium

---

## Final Workload Distribution

| Node                            | Role                             | Workloads                                                                                                                                                                              |
| ------------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **homelab-amd** (always-on)     | Control plane, critical services | pi-hole, home-assistant, open-webui (all incl. redis), bike-weather (all incl. postgres), bike-weather-auth, wol-proxy, central-postgres, monitoring, agents/kagent, mcp, agentgateway |
| **homelab-amd-desktop** (8-22h) | Daytime non-critical             | paperless, actual, mealie (+mcp-server), vinyl-manager (all 3), n8n, bike-weather-preview                                                                                              |
| **homelab-arm-small/large**     | Lightweight only                 | givgroov, portfolio + system DaemonSets (cilium, kured, monitoring, synology-csi, smb-csi)                                                                                             |
| **homelab-gpu** (on-demand)     | GPU workloads                    | Ollama (via WoL proxy, unchanged)                                                                                                                                                      |

---

## File Structure

### New files
- `ansible/roles/k3s-desktop-worker/tasks/main.yml` — K3s agent join with daytime labels/taint
- `apps/wol-scheduler/namespace.yaml` — WoL scheduler namespace
- `apps/wol-scheduler/cronjob-wake.yaml` — CronJob: wake desktop at 07:55
- `apps/wol-scheduler/cronjob-suspend.yaml` — CronJob: suspend desktop at 22:05
- `apps/wol-scheduler/configmap.yaml` — SSH key + config for suspend
- `apps/wol-scheduler/external-secret.yaml` — SSH key from Azure Key Vault
- `apps/wol-scheduler/service-account.yaml` — ServiceAccount for CronJobs
- `apps/wol-scheduler/kustomization.yaml`
- `apps/_argocd/wol-scheduler-app.yaml` — ArgoCD Application

### Modified files
- `ansible/inventory.yml` — Add `homelab-amd-desktop` to new `desktop_workers` group
- `ansible/playbooks/k3s-cluster.yml` — Add desktop workers play
- `apps/_argocd/tetragon-app.yaml` — Add ARM node affinity exclusion
- `apps/_argocd/kagent-app.yaml` — Add affinity to exclude ARM
- `apps/bike-weather/postgres.yaml` — Add `nodeSelector` for homelab-amd
- `apps/open-webui/redis-deployment.yaml` — Add `nodeSelector` for homelab-amd
- `apps/mcp/deployment.yaml` — Add `nodeSelector` for homelab-amd
- `apps/paperless/pvc.yaml` — Change `local-path` → `storage-box-smb`
- `apps/paperless/deployment.yaml` — Add `nodeSelector` + toleration for desktop
- `apps/actual/deployment.yaml` — Add `nodeSelector` + toleration for desktop
- `apps/mealie/deployment.yaml` — Change `nodeSelector` from homelab-amd → desktop + toleration
- `apps/mealie/mcp-server-deployment.yaml` — Change `nodeSelector` from homelab-amd → desktop + toleration
- `apps/vinyl-manager/backend-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/vinyl-manager/frontend-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/vinyl-manager/audio-analyzer-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/n8n/deployment.yaml` — Change `nodeSelector` from homelab-amd → desktop + toleration
- `apps/bike-weather-preview/backend-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/bike-weather-preview/frontend-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/bike-weather-preview/nginx-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/bike-weather-preview/agent-deployment.yaml` — Add `nodeSelector` + toleration
- `apps/givgroov/deployment.yaml` — Add `nodeSelector` for arm64
- `apps/portfolio/deployment.yaml` — Add `nodeSelector` for arm64
- `apps/_argocd/kustomization.yaml` — Add `wol-scheduler-app.yaml`
- `apps/agentgateway/deployment.yaml` or equivalent — Add `nodeSelector` for homelab-amd (if it schedules pods)

---

## Phase 0: Immediate Fixes (no new node needed)

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

---

## Phase 1: Ansible — Provision Desktop Node

### Task 7: Add desktop node to inventory

**Files:**
- Modify: `ansible/inventory.yml`

- [ ] **Step 1: Add `desktop_workers` group**

```yaml
---
all:
  vars:
    ansible_user: timosur
    k3s_version: v1.34.2+k3s1
    cilium_cli_version: v0.18.9

  children:
    k3s_cluster:
      children:
        control_plane:
          hosts:
            homelab-amd:
              ansible_connection: local
              ansible_host: localhost
              node_ip: "{{ lookup('pipe', 'hostname -I | awk \"{print \\$1}\"') }}"

        workers:
          hosts:
            homelab-arm-small:
              ansible_host: homelab-arm-small
              node_ip: "{{ lookup('pipe', 'ssh homelab-arm-small hostname -I | awk \"{print \\$1}\"') }}"
              extra_disabled_services:
                - docker.service
                - dphys-swapfile.service

            homelab-arm-large:
              ansible_host: homelab-arm-large
              node_ip: "{{ lookup('pipe', 'ssh homelab-arm-large hostname -I | awk \"{print \\$1}\"') }}"
              extra_disabled_services:
                - docker.service
                - dphys-swapfile.service

        gpu_workers:
          hosts:
            homelab-gpu:
              ansible_host: 192.168.2.47
              node_ip: "{{ lookup('pipe', 'ssh homelab-gpu hostname -I | awk \"{print \\$1}\"') }}"
              wol_mac: "2c:f0:5d:05:9d:80"

        desktop_workers:
          hosts:
            homelab-amd-desktop:
              ansible_host: 192.168.2.241
              node_ip: "{{ lookup('pipe', 'ssh homelab-amd-desktop hostname -I | awk \"{print \\$1}\"') }}"
              wol_mac: "30:9c:23:8a:30:e3"
```

- [ ] **Step 2: Commit**

```bash
git add ansible/inventory.yml
git commit -m "feat: add homelab-amd-desktop to inventory"
```

---

### Task 8: Create k3s-desktop-worker Ansible role

Based on `k3s-gpu-worker` but with daytime-specific labels/taint instead of GPU taint.

**Files:**
- Create: `ansible/roles/k3s-desktop-worker/tasks/main.yml`

- [ ] **Step 1: Create the role**

```yaml
---
- name: Install open-iscsi for Synology CSI iSCSI support
  ansible.builtin.package:
    name: open-iscsi
    state: present
  become: true

- name: Enable and start iscsid service
  ansible.builtin.systemd:
    name: iscsid
    state: started
    enabled: true
  become: true

- name: Check if k3s is already installed
  ansible.builtin.stat:
    path: /usr/local/bin/k3s
  register: k3s_binary

- name: Get control plane IP
  ansible.builtin.set_fact:
    control_plane_ip: "{{ hostvars['homelab-amd']['node_ip'] }}"

- name: Get k3s token from control plane hostvars
  ansible.builtin.set_fact:
    k3s_token: "{{ hostvars['homelab-amd']['k3s_token'] }}"
  when: hostvars['homelab-amd']['k3s_token'] is defined

- name: Read k3s token directly from control plane (when run with --limit)
  ansible.builtin.slurp:
    src: /var/lib/rancher/k3s/server/node-token
  register: k3s_token_raw
  delegate_to: homelab-amd
  become: true
  when: k3s_token is not defined

- name: Set k3s token from direct read
  ansible.builtin.set_fact:
    k3s_token: "{{ k3s_token_raw.content | b64decode | trim }}"
  when: k3s_token is not defined and k3s_token_raw is not skipped

- name: Verify token is available
  ansible.builtin.fail:
    msg: "K3S token not available from control plane. Ensure control plane role has run successfully."
  when: k3s_token is not defined or k3s_token | length == 0

- name: Debug connection information
  ansible.builtin.debug:
    msg:
      - "Control plane IP: {{ control_plane_ip }}"
      - "Worker node IP: {{ node_ip }}"
      - "K3S Token (first 20 chars): {{ k3s_token[:20] }}..."

- name: Create k3s config directory
  ansible.builtin.file:
    path: /etc/rancher/k3s
    state: directory
    mode: "0755"
  become: true

- name: Create k3s agent config with desktop labels and taints
  ansible.builtin.copy:
    dest: /etc/rancher/k3s/config.yaml
    content: |
      node-label:
        - "availability=daytime"
        - "node.kubernetes.io/gpu=amd"
        - "gpu-type=vulkan"
      node-taint:
        - "availability=daytime:NoSchedule"
    mode: "0644"
  become: true

- name: Install k3s agent
  ansible.builtin.shell: |
    set -o pipefail
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="{{ k3s_version }}" \
      K3S_URL="https://{{ control_plane_ip }}:6443" \
      K3S_TOKEN="{{ k3s_token }}" \
      INSTALL_K3S_EXEC="--node-ip={{ node_ip }}" \
      sh -
  args:
    executable: /bin/bash
    creates: /usr/local/bin/k3s
  become: true
  when: not k3s_binary.stat.exists

- name: Ensure k3s-agent service is running
  ansible.builtin.systemd:
    name: k3s-agent
    state: started
    enabled: true
  become: true

- name: Wait for node to register
  ansible.builtin.pause:
    seconds: 30

- name: Check k3s-agent service status
  ansible.builtin.command: systemctl status k3s-agent --no-pager
  register: k3s_agent_status
  become: true
  changed_when: false
  failed_when: false

- name: Show k3s-agent status
  ansible.builtin.debug:
    var: k3s_agent_status.stdout_lines
```

- [ ] **Step 2: Commit**

```bash
git add ansible/roles/k3s-desktop-worker/tasks/main.yml
git commit -m "feat: add k3s-desktop-worker role with daytime labels/taint"
```

---

### Task 9: Add desktop workers play to k3s-cluster.yml

**Files:**
- Modify: `ansible/playbooks/k3s-cluster.yml`

- [ ] **Step 1: Add desktop workers play after GPU workers, before Cilium**

Add this play between "Setup GPU workers" and "Install and configure Cilium":

```yaml
- name: Setup desktop workers
  hosts: desktop_workers
  gather_facts: true
  become: true
  roles:
    - node-hardening
    - amd-gpu
    - k3s-desktop-worker
```

- [ ] **Step 2: Commit**

```bash
git add ansible/playbooks/k3s-cluster.yml
git commit -m "feat: add desktop workers play to k3s-cluster.yml"
```

---

### Task 10: Setup SSH key auth on desktop node

Before Ansible can manage the node, SSH key auth must be configured (currently password-only).

- [ ] **Step 1: Copy SSH public key to desktop node**

Run from local machine:

```bash
ssh-copy-id -i /Users/timosur/code/homelab/keys/id_ed25519.pub timosur@192.168.2.241
```

- [ ] **Step 2: Verify passwordless SSH works**

```bash
ssh -i /Users/timosur/code/homelab/keys/id_ed25519 timosur@homelab-amd-desktop "hostname"
```

Expected: `homelab-amd-desktop` without password prompt.

- [ ] **Step 3: Verify Ansible connectivity**

```bash
cd ansible
ansible homelab-amd-desktop -i inventory.yml -m ping
```

Expected: `SUCCESS`

---

### Task 11: Run Ansible to provision desktop node

- [ ] **Step 1: Run the playbook for desktop workers only**

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/k3s-cluster.yml --limit desktop_workers
```

- [ ] **Step 2: Verify node joined cluster**

```bash
kubectl get nodes -o wide
```

Expected: `homelab-amd-desktop` with status `Ready`, labels `availability=daytime`, taint `availability=daytime:NoSchedule`.

```bash
kubectl describe node homelab-amd-desktop | grep -A5 'Labels\|Taints'
```

---

## Phase 2: WoL Scheduler

### Task 12: Create WoL scheduler app

A CronJob-based approach to wake and suspend the desktop node on schedule.

**Files:**
- Create: `apps/wol-scheduler/namespace.yaml`
- Create: `apps/wol-scheduler/external-secret.yaml`
- Create: `apps/wol-scheduler/cronjob-wake.yaml`
- Create: `apps/wol-scheduler/cronjob-suspend.yaml`
- Create: `apps/wol-scheduler/kustomization.yaml`
- Create: `apps/_argocd/wol-scheduler-app.yaml`
- Modify: `apps/_argocd/kustomization.yaml`

- [ ] **Step 1: Create namespace.yaml**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: wol-scheduler
```

- [ ] **Step 2: Create external-secret.yaml for SSH key**

Reuse the same SSH key pattern as wol-proxy:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: wol-scheduler-ssh-key
  namespace: wol-scheduler
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: wol-scheduler-ssh-key
  data:
    - secretKey: ssh-key
      remoteRef:
        key: wol-proxy-ssh-key
```

- [ ] **Step 3: Create cronjob-wake.yaml**

Wake the desktop node at 07:55 Europe/Berlin via WoL magic packet. Runs on `homelab-amd` (always on).

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: wake-desktop
  namespace: wol-scheduler
spec:
  schedule: "55 7 * * *"
  timeZone: "Europe/Berlin"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 3
      template:
        spec:
          nodeSelector:
            kubernetes.io/hostname: homelab-amd
          hostNetwork: true
          containers:
            - name: wake
              image: busybox:1.37
              command:
                - sh
                - -c
                - |
                  # Install wakeonlan equivalent using raw packet
                  # busybox doesn't have wakeonlan, use /dev/udp or ether-wake
                  # Use a simple UDP magic packet approach
                  MAC="30:9c:23:8a:30:e3"
                  BROADCAST="192.168.2.255"

                  # Build WoL magic packet: 6x FF + 16x MAC
                  printf '\xff\xff\xff\xff\xff\xff' > /tmp/wol
                  MAC_BYTES=$(echo "$MAC" | sed 's/://g' | sed 's/../\\x&/g')
                  for i in $(seq 1 16); do printf "$MAC_BYTES"; done >> /tmp/wol

                  # Send via netcat UDP
                  cat /tmp/wol | nc -u -w1 -b "$BROADCAST" 9
                  echo "WoL packet sent to $MAC via $BROADCAST"
              resources:
                requests:
                  cpu: 10m
                  memory: 16Mi
                limits:
                  cpu: 50m
                  memory: 32Mi
          restartPolicy: OnFailure
```

- [ ] **Step 4: Create cronjob-suspend.yaml**

Suspend the desktop node at 22:05 Europe/Berlin via SSH.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: suspend-desktop
  namespace: wol-scheduler
spec:
  schedule: "5 22 * * *"
  timeZone: "Europe/Berlin"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          nodeSelector:
            kubernetes.io/hostname: homelab-amd
          initContainers:
            - name: fix-ssh-key-perms
              image: busybox:1.37
              command:
                - sh
                - -c
                - |
                  base64 -d /ssh-secret/ssh-key > /ssh-key/ssh-key
                  chmod 400 /ssh-key/ssh-key
              volumeMounts:
                - name: ssh-key-secret
                  mountPath: /ssh-secret
                  readOnly: true
                - name: ssh-key
                  mountPath: /ssh-key
              resources:
                requests:
                  cpu: 10m
                  memory: 16Mi
                limits:
                  cpu: 50m
                  memory: 32Mi
          containers:
            - name: suspend
              image: alpine:3.21
              command:
                - sh
                - -c
                - |
                  apk add --no-cache openssh-client
                  # Drain node first (cordon + evict pods), then suspend
                  echo "Suspending homelab-amd-desktop..."
                  ssh -o StrictHostKeyChecking=no \
                      -o UserKnownHostsFile=/dev/null \
                      -i /ssh-key/ssh-key \
                      timosur@192.168.2.241 \
                      "sudo systemctl suspend"
                  echo "Suspend command sent"
              volumeMounts:
                - name: ssh-key
                  mountPath: /ssh-key
                  readOnly: true
              resources:
                requests:
                  cpu: 10m
                  memory: 32Mi
                limits:
                  cpu: 100m
                  memory: 64Mi
          volumes:
            - name: ssh-key-secret
              secret:
                secretName: wol-scheduler-ssh-key
            - name: ssh-key
              emptyDir: {}
          restartPolicy: OnFailure
```

- [ ] **Step 5: Create kustomization.yaml**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - namespace.yaml
  - external-secret.yaml
  - cronjob-wake.yaml
  - cronjob-suspend.yaml

namespace: wol-scheduler
```

- [ ] **Step 6: Create ArgoCD Application**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: wol-scheduler
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/timosur/homelab.git
    targetRevision: HEAD
    path: apps/wol-scheduler
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 7: Register in ArgoCD kustomization**

Add `wol-scheduler-app.yaml` to `apps/_argocd/kustomization.yaml` resources list.

- [ ] **Step 8: Verify**

```bash
kubectl get cronjobs -n wol-scheduler
```
Expected: `wake-desktop` (55 7 * * *) and `suspend-desktop` (5 22 * * *)

- [ ] **Step 9: Commit**

```bash
git add apps/wol-scheduler/ apps/_argocd/wol-scheduler-app.yaml apps/_argocd/kustomization.yaml
git commit -m "feat: add WoL scheduler for desktop node (wake 07:55, suspend 22:05)"
```

---

## Phase 3: Migrate Apps to Desktop Node

All apps in this phase use `storage-box-smb`, `hcloud-volumes`, or AppDBClaim — none have `local-path` PVCs bound to specific nodes, so they can freely move.

The standard toleration + nodeSelector block to add:

```yaml
      nodeSelector:
        kubernetes.io/hostname: homelab-amd-desktop
      tolerations:
        - key: "availability"
          operator: "Equal"
          value: "daytime"
          effect: "NoSchedule"
```

### Task 13: Migrate paperless to desktop

Paperless PVCs are `local-path` bound to `homelab-arm-large`. Since it's not in use, delete PVCs and change to `storage-box-smb`.

**Files:**
- Modify: `apps/paperless/pvc.yaml`
- Modify: `apps/paperless/deployment.yaml`

- [ ] **Step 1: Delete existing paperless PVCs**

```bash
kubectl scale deployment paperless -n paperless --replicas=0
kubectl scale deployment paperless-redis -n paperless --replicas=0
kubectl delete pvc paperless-data paperless-media paperless-export paperless-consume -n paperless
```

- [ ] **Step 2: Change PVC storage class to storage-box-smb**

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
  storageClassName: storage-box-smb
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
  storageClassName: storage-box-smb
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
  storageClassName: storage-box-smb
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
  storageClassName: storage-box-smb
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
Expected: Both pods on `homelab-amd-desktop`, PVCs using `storage-box-smb`.

- [ ] **Step 5: Commit**

```bash
git add apps/paperless/pvc.yaml apps/paperless/deployment.yaml
git commit -m "feat: migrate paperless to desktop node with storage-box-smb"
```

---

### Task 14: Migrate actual to desktop

Actual uses `storage-box-smb` — freely movable.

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

### Task 15: Migrate mealie to desktop

Mealie has `storage-box-smb` for data (movable) and `local-path` for postgres (bound to `homelab-amd`). The CNPG postgres stays on `homelab-amd` — mealie pods move to desktop and connect via cluster DNS.

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

### Task 16: Migrate vinyl-manager to desktop

All 3 deployments have no nodeSelector. Postgres uses `storage-box-smb` — freely movable.

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

### Task 17: Migrate n8n to desktop

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

### Task 18: Migrate bike-weather-preview to desktop

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

## Phase 4: Verification

### Task 19: End-to-end verification

- [ ] **Step 1: Verify node distribution**

```bash
kubectl get pods -A -o wide | awk '{print $1, $2, $8}' | sort -k3
```

Check that:
- `homelab-amd`: pi-hole, home-assistant, open-webui (all), bike-weather (all incl. postgres), bike-weather-auth, wol-proxy, central-postgres, monitoring, agents, mcp
- `homelab-amd-desktop`: paperless (both), actual, mealie (+mcp-server), vinyl-manager (all 3), n8n (if scaled up), bike-weather-preview (if scaled up)
- `homelab-arm-small/large`: givgroov, portfolio + DaemonSets only
- `homelab-gpu`: kured DaemonSet only (Ollama is outside K8s)

- [ ] **Step 2: Verify no CrashLoopBackOff**

```bash
kubectl get pods -A --field-selector status.phase!=Running,status.phase!=Succeeded | grep -v Completed
```

- [ ] **Step 3: Verify tetragon excluded from ARM**

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=tetragon -o wide
```

Expected: No pods on `homelab-arm-small` or `homelab-arm-large`.

- [ ] **Step 4: Test WoL scheduling manually**

```bash
# Trigger wake job manually
kubectl create job --from=cronjob/wake-desktop test-wake -n wol-scheduler
kubectl logs job/test-wake -n wol-scheduler
# Verify node comes online
kubectl get node homelab-amd-desktop
```

- [ ] **Step 5: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: complete desktop node integration and workload redistribution"
git push
```
