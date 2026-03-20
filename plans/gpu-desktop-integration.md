# Plan: Integrate AMD GPU Desktop PC into K3s Cluster

## Summary

Add a desktop PC with AMD GPU as a K3s worker node for GPU-accelerated LLM inference (Ollama) and other GPU workloads (image generation, transcription). Uses AMD ROCm stack for GPU passthrough to containers. Includes Wake-on-LAN support since the desktop may not be always-on. Replaces the Mac Mini Ollama plan.

## Prerequisites (before any implementation)

- ✅ **AMD GPU model confirmed** — RX 5700 XT (RDNA 1 / Navi 10). ROCm support is unofficial but functional with `HSA_OVERRIDE_GFX_VERSION=10.3.0` environment variable.
- ✅ **Static IP assigned** — `192.168.2.47`
- **Enable Wake-on-LAN** in the desktop's BIOS/UEFI and note MAC address

## Phase 1: OS & Base Setup

1. Install **Ubuntu 24.04 LTS Server** on the desktop (best ROCm support, no desktop environment needed)
2. Configure static IP, hostname (`homelab-gpu`), SSH key access with existing key from `keys/id_ed25519.pub`
3. Enable WoL at OS level — `ethtool -s <iface> wol g`, persisted via systemd or netplan

## Phase 2: AMD ROCm Drivers (new Ansible role)

> **Note**: ROCm 7.2 is already installed manually on `homelab-gpu`. This Ansible role codifies the setup for future reprovisioning.

4. **Create `ansible/roles/amd-gpu/`** — installs AMD ROCm 7.2 via `amdgpu-install` package, configures `render`/`video` groups, verifies with `rocm-smi`

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
- Install `rocm` metapackage
- Create `/etc/profile.d/rocm-env.sh` with `HSA_OVERRIDE_GFX_VERSION=10.3.0` (required for RX 5700 XT)
- Verify GPU detection with `rocm-smi`
- Handler to reboot if DKMS module was installed/updated

### RX 5700 XT specific config

The 5700 XT (gfx1010) requires GFX version override to work with ROCm:

```bash
# /etc/profile.d/rocm-env.sh
export HSA_OVERRIDE_GFX_VERSION=10.3.0
```

This must also be set in container workloads (see Phase 5).

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
   - Applies node labels on join: `node.kubernetes.io/gpu=amd`, `gpu-type=rocm`
   - Applies node taints: `gpu=true:NoSchedule` (prevents non-GPU workloads from landing on GPU nodes)
   - Configures containerd with CDI (Container Device Interface) for AMD GPU device passthrough
   - Does NOT blacklist GPU drivers (unlike `gpu-blacklist` role on control plane)

### K3s agent config

```yaml
# /etc/rancher/k3s/config.yaml on GPU workers
node-label:
  - "node.kubernetes.io/gpu=amd"
  - "gpu-type=rocm"
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

## Phase 4: AMD GPU Device Plugin (new app)

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

| Setting      | Current                               | New                                           |
| ------------ | ------------------------------------- | --------------------------------------------- |
| Image        | `ollama/ollama:latest`                | `ollama/ollama:rocm`                          |
| GPU resource | (none)                                | `amd.com/gpu: 1` in limits                    |
| nodeSelector | `kubernetes.io/hostname: homelab-amd` | `node.kubernetes.io/gpu: amd`                 |
| Toleration   | (none)                                | `gpu=true:NoSchedule`                         |
| Memory limit | `4Gi`                                 | `16Gi` (GPU models need more RAM for loading) |
| CPU limit    | `2000m`                               | `4000m`                                       |
| Env var      | (none)                                | `HSA_OVERRIDE_GFX_VERSION=10.3.0`             |

> **RX 5700 XT note**: The `HSA_OVERRIDE_GFX_VERSION` env var is required for ROCm to recognize the gfx1010 architecture.

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

- [ ] `rocm-smi` on GPU node shows AMD GPU detected with temperature/utilization
- [ ] `kubectl get nodes` — GPU node shows `Ready` with label `node.kubernetes.io/gpu=amd`
- [ ] `kubectl describe node homelab-gpu` — shows `amd.com/gpu: 1` in Allocatable resources
- [ ] Non-GPU pods do NOT schedule on GPU nodes (taint enforcement)
- [ ] Ollama pod logs show ROCm initialization and GPU detection
- [ ] `rocm-smi` on GPU node shows utilization during model inference
- [ ] ArgoCD shows `amd-gpu-device-plugin` app synced and healthy
- [ ] ArgoCD shows `wol-proxy` app synced and healthy
- [ ] WoL proxy test: suspend GPU node, send chat request → node wakes automatically
- [ ] Idle timeout test: leave GPU idle for 30 min → node auto-suspends
- [ ] Open WebUI chat works seamlessly (user unaware of wake/sleep)

## Architecture Decisions

| Decision                           | Rationale                                                               |
| ---------------------------------- | ----------------------------------------------------------------------- |
| **Ubuntu 24.04 Server**            | Best ROCm driver support, matches amd64 arch of control plane           |
| **K3s workers** (not external)     | Enables native Kubernetes GPU scheduling for multiple workload types    |
| **Taint GPU nodes**                | Prevents non-GPU workloads from consuming expensive GPU node resources  |
| **ROCm** (not OpenCL)              | AMD's full compute platform with first-class Ollama and PyTorch support |
| **CDI for device passthrough**     | Modern containerd-native approach, no need for custom runtime shim      |
| **Separate `k3s-gpu-worker` role** | GPU workers need different config than ARM workers; keeps roles clean   |
| **WoL proxy on control plane**     | Control plane always on; transparent wake/sleep for GPU workloads       |
| **Ollama hostNetwork**             | Allows wol-proxy to reach Ollama directly at node IP for wake detection |

## Risks & Mitigations

| Risk                                     | Mitigation                                                                           |
| ---------------------------------------- | ------------------------------------------------------------------------------------ |
| RX 5700 XT ROCm unofficial support       | Already tested and working; use `HSA_OVERRIDE_GFX_VERSION=10.3.0` workaround         |
| ROCm driver instability on newer kernels | Pin Ubuntu HWE kernel version                                                        |
| GPU node unavailability (WoL wake time)  | WoL proxy handles on-demand wake; ~60-120s cold start                                |
| Power consumption when idle              | WoL proxy auto-suspends after configurable idle timeout                              |
| Ollama model storage on GPU node         | Use `hcloud-volumes` (Synology iSCSI) for PVC so models persist across node restarts |

## Future Enhancements (not in scope)

- Add second GPU node for multi-GPU model parallelism (vLLM or similar)
- GPU monitoring in Prometheus/Grafana (ROCm exporter)
- Additional GPU workloads: Stable Diffusion, Whisper transcription (just add to wol-proxy config)
