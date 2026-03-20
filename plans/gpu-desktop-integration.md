# Plan: Integrate AMD GPU Desktop PC into K3s Cluster

## Summary

Add a desktop PC with AMD GPU as a K3s worker node for GPU-accelerated LLM inference (Ollama) and other GPU workloads (image generation, transcription). Uses **Vulkan backend** for Ollama (ROCm HIP is unstable on RX 5700 XT). Includes Wake-on-LAN support since the desktop may not be always-on. Replaces the Mac Mini Ollama plan.

## Prerequisites (before any implementation)

- ✅ **AMD GPU model confirmed** — RX 5700 XT (RDNA 1 / Navi 10). ROCm HIP compute is unstable on gfx1010, but **Vulkan backend works perfectly**.
- ✅ **Static IP assigned** — `192.168.2.47`
- ✅ **MAC address** — `2c:f0:5d:05:9d:80`
- **Enable Wake-on-LAN** in the desktop's BIOS/UEFI

## Phase 1: OS & Base Setup

1. Install **Ubuntu 24.04 LTS Server** on the desktop (best ROCm support, no desktop environment needed)
2. Configure static IP, hostname (`homelab-gpu`), SSH key access with existing key from `keys/id_ed25519.pub`
3. Enable WoL at OS level — `ethtool -s <iface> wol g`, persisted via systemd or netplan

## Phase 2: AMD GPU Drivers (new Ansible role)

> **Note**: ROCm 7.2 is installed for monitoring tools (rocm-smi), but Ollama uses Vulkan backend for actual GPU compute. ROCm HIP crashes with "illegal memory access" errors on the RX 5700 XT (gfx1010).

4. **Create `ansible/roles/amd-gpu/`** — installs AMD ROCm 7.2 for monitoring, configures `render`/`video` groups, verifies GPU detection

### Role structure

```
ansible/roles/amd-gpu/
├── tasks/
│   └── main.yml
├── handlers/
│   └── main.yml
├── templates/
│   └── rocm-env.sh.j2
└── defaults/
    └── main.yml
```

### Key tasks

- Download and install `amdgpu-install_7.2.70200-1_all.deb` from AMD repo
- Install `python3-setuptools`, `python3-wheel` (ROCm dependencies)
- Add `timosur` user to `render` and `video` groups
- Install `rocm` metapackage (for rocm-smi monitoring)
- Set PATH to include `/opt/rocm/bin`
- Verify GPU detection with `rocm-smi`
- Handler to reboot if DKMS module was installed/updated

### RX 5700 XT GPU acceleration

The 5700 XT (gfx1010) works best with **Vulkan backend**, not ROCm HIP:

```bash
# In Ollama systemd service or container env
OLLAMA_VULKAN=1
OLLAMA_LLM_LIBRARY=vulkan
```

ROCm HIP causes "illegal memory access" errors because gfx1010 is unofficially supported. Vulkan uses Mesa's RADV driver which has excellent Navi support.

## Phase 3: K3s Worker Join with GPU Config

5. **Add node to `ansible/inventory.yml`** — new `gpu_workers` group:

```yaml
gpu_workers:
  hosts:
    homelab-gpu:
      ansible_host: 192.168.2.47
      arch: amd64
      wol_mac: "XX:XX:XX:XX:XX:XX"  # fill in
  vars:
    ansible_user: timosur
```

6. **Create `ansible/roles/k3s-gpu-worker/`** — joins K3s with GPU-specific configuration:
   - Installs K3s agent (same method as existing worker role)
   - Installs `open-iscsi` for Synology CSI compatibility
   - Applies node labels on join: `node.kubernetes.io/gpu=amd`, `gpu-type=vulkan`
   - Applies node taints: `gpu=true:NoSchedule` (prevents non-GPU workloads from landing on GPU nodes)
   - Does NOT blacklist GPU drivers (unlike `gpu-blacklist` role on control plane)

### K3s agent config

```yaml
# /etc/rancher/k3s/config.yaml on GPU workers
node-label:
  - "node.kubernetes.io/gpu=amd"
  - "gpu-type=vulkan"
node-taint:
  - "gpu=true:NoSchedule"
```

7. **Update `ansible/playbooks/k3s-cluster.yml`** — add plays for GPU workers:

```yaml
- name: Setup GPU workers
  hosts: gpu_workers
  become: true
  roles:
    - node-hardening
    - amd-gpu
    - k3s-gpu-worker
```

**Important**: Do NOT apply `gpu-blacklist` role to GPU workers (that role disables Intel iGPU on the control plane).

## Phase 4: AMD GPU Device Plugin (optional)

> **Note**: The k8s-device-plugin is NOT required for Vulkan backend. Vulkan accesses GPU via `/dev/dri` which is available in privileged containers. This phase is optional and only needed if you want `amd.com/gpu` resource scheduling for ROCm workloads.

8. **Create `apps/amd-gpu-device-plugin/`** with Kustomize manifests:

```
apps/amd-gpu-device-plugin/
├── kustomization.yaml
├── namespace.yaml
└── daemonset.yaml
```

- DaemonSet running `rocm/k8s-device-plugin` image
- `nodeSelector: node.kubernetes.io/gpu: amd` — only runs on GPU nodes
- Tolerates `gpu=true:NoSchedule` taint
- Exposes `amd.com/gpu` as a schedulable Kubernetes resource
- Mounts `/dev/dri` and `/dev/kfd` from host

9. **Create ArgoCD app** `apps/_argocd/amd-gpu-device-plugin-app.yaml` and add to `apps/_argocd/kustomization.yaml`

## Phase 5: Ollama GPU Acceleration

10. **Update `apps/open-webui/ollama-statefulset.yaml`**:

| Setting      | Current                               | New                                            |
| ------------ | ------------------------------------- | ---------------------------------------------- |
| Image        | `ollama/ollama:latest`                | `ollama/ollama:latest` (unchanged)             |
| GPU backend  | (none)                                | `OLLAMA_VULKAN=1`, `OLLAMA_LLM_LIBRARY=vulkan` |
| nodeSelector | `kubernetes.io/hostname: homelab-amd` | `node.kubernetes.io/gpu: amd`                  |
| Toleration   | (none)                                | `gpu=true:NoSchedule`                          |
| Memory limit | `4Gi`                                 | `16Gi` (GPU models need more RAM for loading)  |
| CPU limit    | `2000m`                               | `4000m`                                        |
| Security     | (none)                                | `privileged: true` (for /dev/dri access)       |

> **RX 5700 XT note**: Vulkan backend (~30 tokens/sec) is stable and fast. ROCm HIP crashes with "illegal memory access" errors.

11. **Open WebUI connects to wol-proxy** — the proxy handles wake/sleep and forwards to Ollama

## Phase 6: WoL Proxy Service

The WoL proxy runs on the control plane (always on) and provides:
- **On-demand wake**: If GPU node is sleeping, sends WoL magic packet and waits for backend
- **Idle auto-sleep**: After X minutes of no requests, SSH to node and suspend
- **Multi-backend support**: ConfigMap-driven, can proxy any GPU service (Ollama, ComfyUI, Whisper, etc.)

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Control Plane (homelab-amd) - always on                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐ │
│  │  wol-proxy (Deployment)                                    │ │
│  │                                                            │ │
│  │  ConfigMap-driven backends:                                │ │
│  │  - ollama: :11434 → 192.168.2.47:11434                    │ │
│  │                                                            │ │
│  │  On request → TCP probe → WoL if needed → forward          │ │
│  │  On idle timeout → SSH suspend                             │ │
│  └───────────────────────────────────────────────────────────┘ │
│                                                                 │
│  Open WebUI → ollama-service (ClusterIP) → wol-proxy:11434     │
└─────────────────────────────────────────────────────────────────┘
         │
         │ WoL / SSH suspend
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  GPU Node (homelab-gpu) - sleeps when idle                      │
│  - Ollama :11434                                                │
│  - (future: ComfyUI :8188, Whisper :9000)                      │
└─────────────────────────────────────────────────────────────────┘
```

12. **Create `apps/wol-proxy/`** with:

### App structure

```
apps/wol-proxy/
├── kustomization.yaml
├── namespace.yaml
├── deployment.yaml
├── service-ollama.yaml      # ClusterIP exposing :11434
├── configmap.yaml           # Backend definitions
└── external-secret.yaml     # SSH key for suspend
```

### ConfigMap example

```yaml
backends:
  - name: ollama
    listenPort: 11434
    targetHost: 192.168.2.47
    targetPort: 11434
    wolMac: "2c:f0:5d:05:9d:80"
    wolBroadcast: "192.168.2.255"
    idleTimeoutMinutes: 30
    wakeTimeoutSeconds: 120
    sshUser: timosur
```

13. **Update `apps/open-webui/`** — change Ollama service reference:
    - Point to `ollama-service.wol-proxy.svc.cluster.local:11434` (the proxy's service)
    - Or keep the same service name in `open-webui` namespace pointing to the proxy

14. **Store SSH key in Azure Key Vault** as `wol-proxy-ssh-key` for suspend capability

## Phase 7: Cleanup

15. **Delete `plans/mac-mini-ollama.md`** — replaced by this plan
16. **Update README.md** — document GPU nodes in architecture section
17. **Update `ONBOARDING_GUIDE.md`** if GPU scheduling patterns should be documented

## Verification Checklist

- [x] `rocm-smi` on GPU node shows AMD GPU detected (RX 5700 XT, gfx1010)
- [ ] `kubectl get nodes` — GPU node shows `Ready` with label `node.kubernetes.io/gpu=amd`
- [x] Ollama logs show Vulkan GPU detection: "AMD Radeon RX 5700 XT (RADV NAVI10)" with 8GB VRAM
- [x] `ollama ps` shows model loaded with `100% GPU` processor
- [x] Inference works without crashes (~30 tokens/sec on mistral:7b)
- [ ] Non-GPU pods do NOT schedule on GPU nodes (taint enforcement)
- [ ] ArgoCD shows `wol-proxy` app synced and healthy
- [ ] WoL proxy test: suspend GPU node, send chat request → node wakes automatically
- [ ] Idle timeout test: leave GPU idle for 30 min → node auto-suspends
- [ ] Open WebUI chat works seamlessly (user unaware of wake/sleep)

## Architecture Decisions

| Decision                           | Rationale                                                               |
| ---------------------------------- | ----------------------------------------------------------------------- |
| **Ubuntu 24.04 Server**            | Modern Mesa Vulkan drivers, ROCm for monitoring tools                   |
| **K3s workers** (not external)     | Enables native Kubernetes GPU scheduling for multiple workload types    |
| **Taint GPU nodes**                | Prevents non-GPU workloads from consuming expensive GPU node resources  |
| **Vulkan** (not ROCm HIP)          | ROCm HIP crashes on gfx1010; Vulkan is stable and fast (~30 tok/s)      |
| **Privileged container**           | Required for Vulkan to access /dev/dri GPU devices                      |
| **Separate `k3s-gpu-worker` role** | GPU workers need different config than ARM workers; keeps roles clean   |
| **WoL proxy on control plane**     | Control plane always on; transparent wake/sleep for GPU workloads       |
| **Ollama hostNetwork**             | Allows wol-proxy to reach Ollama directly at node IP for wake detection |

## Risks & Mitigations

| Risk                                    | Mitigation                                                                           |
| --------------------------------------- | ------------------------------------------------------------------------------------ |
| RX 5700 XT ROCm HIP unstable            | Use Vulkan backend instead; tested and stable at ~30 tokens/sec                      |
| Vulkan performance vs HIP               | Vulkan is competitive; may be slightly slower but stable is better than crashing     |
| GPU node unavailability (WoL wake time) | WoL proxy handles on-demand wake; ~60-120s cold start                                |
| Power consumption when idle             | WoL proxy auto-suspends after configurable idle timeout                              |
| Ollama model storage on GPU node        | Use `hcloud-volumes` (Synology iSCSI) for PVC so models persist across node restarts |

## Future Enhancements (not in scope)

- Add second GPU node for multi-GPU model parallelism (vLLM or similar)
- GPU monitoring in Prometheus/Grafana (ROCm exporter)
- Additional GPU workloads: Stable Diffusion, Whisper transcription (just add to wol-proxy config)
