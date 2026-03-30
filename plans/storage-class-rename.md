# Storage Class Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `hcloud-volumes` and `storage-box-smb` StorageClasses with `homelab-iscsi`, and create a new `homelab-smb` StorageClass backed by the SMB CSI driver.

**Architecture:** Approach A — each CSI driver owns its StorageClasses. The Synology iSCSI driver defines `homelab-iscsi`, the SMB CSI driver defines `homelab-smb`. All existing consumers are updated. `homelab-smb` becomes the cluster default.

**Tech Stack:** Kubernetes StorageClass, ExternalSecrets, Kustomize, SMB CSI driver (`smb.csi.k8s.io`), Synology CSI driver (`csi.san.synology.com`)

---

### Task 1: Replace iSCSI StorageClasses with `homelab-iscsi`

**Files:**
- Modify: `apps/synology-csi-driver/storage-class.yaml`

- [ ] **Step 1: Add `homelab-iscsi` and keep old classes as aliases**

Replace the entire contents of `apps/synology-csi-driver/storage-class.yaml` with:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: homelab-iscsi
  annotations:
    storageclass.kubernetes.io/is-default-class: "false"
parameters:
  dsm: "192.168.1.26"
  location: "/volume1"
  fsType: "btrfs"
  formatOptions: "-K"
  protocol: iSCSI
provisioner: csi.san.synology.com
reclaimPolicy: Retain
allowVolumeExpansion: true
---
# DEPRECATED: alias kept for existing PVCs. Remove after all PVCs are migrated to homelab-iscsi.
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: hcloud-volumes
  annotations:
    storageclass.kubernetes.io/is-default-class: "false"
parameters:
  dsm: "192.168.1.26"
  location: "/volume1"
  fsType: "btrfs"
  formatOptions: "-K"
  protocol: iSCSI
provisioner: csi.san.synology.com
reclaimPolicy: Retain
allowVolumeExpansion: true
---
# DEPRECATED: alias kept for existing PVCs. Remove after all PVCs are migrated to homelab-iscsi.
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: storage-box-smb
  annotations:
    storageclass.kubernetes.io/is-default-class: "false"
parameters:
  dsm: "192.168.1.26"
  location: "/volume1"
  fsType: "btrfs"
  formatOptions: "-K"
  protocol: iSCSI
provisioner: csi.san.synology.com
reclaimPolicy: Retain
allowVolumeExpansion: true
```

- [ ] **Step 2: Commit**

```bash
git add apps/synology-csi-driver/storage-class.yaml
git commit -m "feat: add homelab-iscsi, keep old classes as deprecated aliases"
```

---

### Task 2: Create `homelab-smb` StorageClass and ExternalSecret

**Files:**
- Create: `apps/smb-csi-driver/storage-class.yaml`
- Create: `apps/smb-csi-driver/external-secret.yaml`
- Modify: `apps/smb-csi-driver/kustomization.yaml`

- [ ] **Step 1: Create SMB ExternalSecret**

Create `apps/smb-csi-driver/external-secret.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: smb-creds
  namespace: kube-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: smb-creds
    creationPolicy: Owner
    template:
      data:
        username: "{{ .synology_username }}"
        password: "{{ .synology_password }}"
  data:
    - secretKey: synology_username
      remoteRef:
        key: synology-csi-username
    - secretKey: synology_password
      remoteRef:
        key: synology-csi-password
```

- [ ] **Step 2: Create SMB StorageClass**

Create `apps/smb-csi-driver/storage-class.yaml`:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: homelab-smb
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: smb.csi.k8s.io
parameters:
  source: "//192.168.1.26/k3s_volumes"
  csi.storage.k8s.io/provisioner-secret-name: smb-creds
  csi.storage.k8s.io/provisioner-secret-namespace: kube-system
  csi.storage.k8s.io/node-stage-secret-name: smb-creds
  csi.storage.k8s.io/node-stage-secret-namespace: kube-system
reclaimPolicy: Retain
volumeBindingMode: Immediate
allowVolumeExpansion: true
mountOptions:
  - dir_mode=0777
  - file_mode=0777
  - noperm
  - mfsymlinks
  - cache=strict
  - noserverino
```

- [ ] **Step 3: Register new files in kustomization.yaml**

Add `external-secret.yaml` and `storage-class.yaml` to the `resources:` list in `apps/smb-csi-driver/kustomization.yaml`, after the existing upstream resources:

```yaml
resources:
  - https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/v1.18.0/deploy/rbac-csi-smb.yaml
  - https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/v1.18.0/deploy/csi-smb-driver.yaml
  - https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/v1.18.0/deploy/csi-smb-controller.yaml
  - https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/v1.18.0/deploy/csi-smb-node.yaml
  - https://raw.githubusercontent.com/kubernetes-csi/csi-driver-smb/v1.18.0/deploy/csi-smb-node-windows.yaml
  - external-secret.yaml
  - storage-class.yaml
```

**Note:** The ExternalSecret and StorageClass must NOT be namespaced to `kube-system` by the kustomization — ExternalSecret needs `kube-system` namespace set explicitly in its manifest (done above), and StorageClass is cluster-scoped (no namespace). The existing `namespace: kube-system` in kustomization.yaml will apply to both, which is correct for ExternalSecret and harmless for StorageClass (cluster-scoped resources ignore namespace).

- [ ] **Step 4: Commit**

```bash
git add apps/smb-csi-driver/
git commit -m "feat: add homelab-smb storage class with SMB CSI driver"
```

---

### Task 3: Update all consumer references from old names to `homelab-iscsi`

**Files:**
- Modify: `apps/n8n/pvc.yaml:9`
- Modify: `apps/bike-weather-auth/media-pvc.yaml:9`
- Modify: `apps/garden/postgres.yaml:28`
- Modify: `apps/open-webui/postgres.yaml:29`
- Modify: `apps/postgres/postgres.yaml:26`
- Modify: `apps/bike-weather/postgres.yaml:25`
- Modify: `apps/mealie/pvc.yaml:12`
- Modify: `apps/vinyl-manager/postgres.yaml:22`
- Modify: `apps/actual/pvc.yaml:12`

- [ ] **Step 1: Update `hcloud-volumes` → `homelab-iscsi` in PVCs**

In `apps/n8n/pvc.yaml`, change:
```yaml
  storageClassName: hcloud-volumes
```
to:
```yaml
  storageClassName: homelab-iscsi
```

In `apps/bike-weather-auth/media-pvc.yaml`, change:
```yaml
  storageClassName: hcloud-volumes
```
to:
```yaml
  storageClassName: homelab-iscsi
```

- [ ] **Step 2: Update `hcloud-volumes` → `homelab-iscsi` in CNPG Cluster CRDs**

In `apps/garden/postgres.yaml`, change:
```yaml
    storageClass: hcloud-volumes
```
to:
```yaml
    storageClass: homelab-iscsi
```

In `apps/open-webui/postgres.yaml`, change:
```yaml
    storageClass: hcloud-volumes
```
to:
```yaml
    storageClass: homelab-iscsi
```

In `apps/postgres/postgres.yaml`, change:
```yaml
    storageClass: hcloud-volumes
```
to:
```yaml
    storageClass: homelab-iscsi
```

In `apps/bike-weather/postgres.yaml`, change:
```yaml
    storageClass: hcloud-volumes
```
to:
```yaml
    storageClass: homelab-iscsi
```

- [ ] **Step 3: Update `storage-box-smb` → `homelab-iscsi` in PVCs and CNPG**

In `apps/mealie/pvc.yaml`, change:
```yaml
  storageClassName: storage-box-smb
```
to:
```yaml
  storageClassName: homelab-iscsi
```

In `apps/actual/pvc.yaml`, change:
```yaml
  storageClassName: storage-box-smb
```
to:
```yaml
  storageClassName: homelab-iscsi
```

In `apps/vinyl-manager/postgres.yaml`, change:
```yaml
    storageClass: storage-box-smb
```
to:
```yaml
    storageClass: homelab-iscsi
```

- [ ] **Step 4: Commit**

```bash
git add apps/n8n/pvc.yaml apps/bike-weather-auth/media-pvc.yaml apps/garden/postgres.yaml \
  apps/open-webui/postgres.yaml apps/postgres/postgres.yaml apps/bike-weather/postgres.yaml \
  apps/mealie/pvc.yaml apps/actual/pvc.yaml apps/vinyl-manager/postgres.yaml
git commit -m "refactor: update all storage class references to homelab-iscsi"
```

---

### Task 4: Update documentation and skills

**Files:**
- Modify: `.github/copilot-instructions.md:43`
- Modify: `.github/skills/cnpg-migration/SKILL.md:21`
- Modify: `.github/skills/homelab-onboarding/SKILL.md:58`

- [ ] **Step 1: Update copilot-instructions.md**

In `.github/copilot-instructions.md` line 43, change:
```
**Storage**: Two storage classes — `hcloud-volumes` (Synology iSCSI, default) and `storage-box-smb` (SMB/NAS).
```
to:
```
**Storage**: Two storage classes — `homelab-iscsi` (Synology iSCSI via `csi.san.synology.com`) and `homelab-smb` (SMB via `smb.csi.k8s.io`, default).
```

- [ ] **Step 2: Update cnpg-migration skill**

In `.github/skills/cnpg-migration/SKILL.md` line 21, change:
```
  `enableSuperuserAccess: true`, storage on `hcloud-volumes`
```
to:
```
  `enableSuperuserAccess: true`, storage on `homelab-iscsi`
```

- [ ] **Step 3: Update homelab-onboarding skill**

In `.github/skills/homelab-onboarding/SKILL.md` line 58, change:
```
   - If yes: storage size, storage class (`hcloud-volumes` default, or `storage-box-smb` for
     large shared data)
```
to:
```
   - If yes: storage size, storage class (`homelab-smb` default, or `homelab-iscsi` for
     iSCSI-backed storage)
```

- [ ] **Step 4: Commit**

```bash
git add .github/copilot-instructions.md .github/skills/cnpg-migration/SKILL.md .github/skills/homelab-onboarding/SKILL.md
git commit -m "docs: update storage class names in docs and skills"
```

---

## Important Notes

**Transition strategy:** Old StorageClasses (`hcloud-volumes`, `storage-box-smb`) are kept as deprecated aliases in `apps/synology-csi-driver/storage-class.yaml`. Existing PVCs in the cluster still reference these names and will continue working. New PVCs (from updated manifests) will use `homelab-iscsi`. Once all existing PVCs have been recycled (deleted and recreated), remove the deprecated aliases and commit:

```bash
# After verifying no PVCs reference old names:
kubectl get pvc -A -o jsonpath='{range .items[*]}{.spec.storageClassName}{"\n"}{end}' | sort | uniq -c
# Then remove the deprecated aliases from apps/synology-csi-driver/storage-class.yaml
```

**Plans directory:** The files in `plans/` (e.g., `desktop-node-integration.md`, `agents.md`) reference old names but are historical planning documents. They do not affect the running cluster and should not be updated.
