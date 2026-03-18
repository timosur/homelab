# Plan: Integrate AMD GPU Desktop PCs into K3s Cluster

## Summary

Add two desktop PCs with AMD GPUs as K3s worker nodes for GPU-accelerated LLM inference (Ollama) and other GPU workloads (image generation, transcription). Uses AMD ROCm stack for GPU passthrough to containers. Includes Wake-on-LAN support since desktops may not be always-on. Replaces the Mac Mini Ollama plan.

## Prerequisites (before any implementation)

- **Identify AMD GPU models** — run `lspci | grep -i vga` on both PCs to check ROCm compatibility. ROCm officially supports RX 7000/6000 series (RDNA 2/3); older cards have limited/no support. **This is a blocker.**
- **Assign static IPs** on 192.168.2.x for both desktops (e.g., `.60`, `.61`)
- **Enable Wake-on-LAN** in each desktop's BIOS/UEFI and note MAC addresses

## Phase 1: OS & Base Setup

1. Install **Ubuntu 24.04 LTS Server** on both desktops (best ROCm support, no desktop environment needed)
2. Configure static IP, hostname (`homelab-gpu-1`, `homelab-gpu-2`), SSH key access with existing key from `keys/id_ed25519.pub`
3. Enable WoL at OS level — `ethtool -s <iface> wol g`, persisted via systemd or netplan

## Phase 2: AMD ROCm Drivers (new Ansible role)

4. **Create `ansible/roles/amd-gpu/`** — installs AMD ROCm drivers (`amdgpu-dkms` + ROCm runtime) via AMD's official apt repo, configures `render`/`video` groups, verifies with `rocm-smi`

### Role structure

```
ansible/roles/amd-gpu/
├── tasks/
│   └── main.yml
├── handlers/
│   └── main.yml
└── defaults/
    └── main.yml
```

### Key tasks

- Add AMD ROCm apt repository and GPG key
- Install `amdgpu-dkms`, `rocm-hip-runtime`, `rocm-smi-lib`
- Add `timosur` user to `render` and `video` groups
- Verify GPU detection with `rocm-smi`
- Handler to reboot if DKMS module was installed/updated

## Phase 3: K3s Worker Join with GPU Config

5. **Add nodes to `ansible/inventory.yml`** — new `gpu_workers` group:

```yaml
gpu_workers:
  hosts:
    homelab-gpu-1:
      ansible_host: 192.168.2.60
      arch: amd64
      wol_mac: "XX:XX:XX:XX:XX:XX"  # fill in
    homelab-gpu-2:
      ansible_host: 192.168.2.61
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

11. **Open WebUI stays unchanged** — connects to Ollama via same `ollama-service` DNS name

## Phase 6: Wake-on-LAN Integration

12. **Create WoL utility on control plane** (`homelab-amd`, always on):
    - Install `wakeonlan` package via Ansible
    - Create script `/usr/local/bin/wake-gpu-nodes.sh` that reads MAC addresses and sends magic packets
    - Optional: CronJob to wake GPU nodes on a schedule (e.g., 8am) and a script to suspend them (e.g., midnight)
    - Store MAC addresses in Ansible inventory (already added in Phase 3)

13. **Configure Kubernetes tolerations** for GPU node unavailability:
    - K3s default `node.kubernetes.io/not-ready` toleration is 300s — GPU pods will wait 5 minutes before being evicted when a node sleeps
    - Consider setting `tolerationSeconds: 3600` on Ollama StatefulSet if GPU nodes regularly sleep/wake

## Phase 7: Cleanup

14. **Delete `plans/mac-mini-ollama.md`** — replaced by this plan
15. **Update README.md** — document GPU nodes in architecture section
16. **Update `ONBOARDING_GUIDE.md`** if GPU scheduling patterns should be documented

## Verification Checklist

- [ ] `rocm-smi` on each GPU node shows AMD GPU detected with temperature/utilization
- [ ] `kubectl get nodes` — both GPU nodes show `Ready` with labels `node.kubernetes.io/gpu=amd`
- [ ] `kubectl describe node homelab-gpu-1` — shows `amd.com/gpu: 1` in Allocatable resources
- [ ] Non-GPU pods do NOT schedule on GPU nodes (taint enforcement)
- [ ] Ollama pod logs show ROCm initialization and GPU detection
- [ ] `rocm-smi` on GPU node shows utilization during model inference
- [ ] Open WebUI chat is noticeably faster than CPU-only baseline
- [ ] WoL: magic packet from control plane → desktop wakes → node rejoins cluster within ~2 min
- [ ] ArgoCD shows `amd-gpu-device-plugin` app synced and healthy

## Architecture Decisions

| Decision                           | Rationale                                                               |
| ---------------------------------- | ----------------------------------------------------------------------- |
| **Ubuntu 24.04 Server**            | Best ROCm driver support, matches amd64 arch of control plane           |
| **K3s workers** (not external)     | Enables native Kubernetes GPU scheduling for multiple workload types    |
| **Taint GPU nodes**                | Prevents non-GPU workloads from consuming expensive GPU node resources  |
| **ROCm** (not OpenCL)              | AMD's full compute platform with first-class Ollama and PyTorch support |
| **CDI for device passthrough**     | Modern containerd-native approach, no need for custom runtime shim      |
| **Separate `k3s-gpu-worker` role** | GPU workers need different config than ARM workers; keeps roles clean   |

## Risks & Mitigations

| Risk                                     | Mitigation                                                                                          |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------- |
| GPUs not ROCm-compatible (pre-RDNA)      | Check `lspci` output before starting. Fallback: use `llama.cpp` with Vulkan backend instead of ROCm |
| ROCm driver instability on newer kernels | Pin Ubuntu HWE kernel version; test driver install on one node first                                |
| GPU node unavailability (WoL wake time)  | Generous tolerationSeconds; StatefulSet won't reschedule to CPU automatically                       |
| Power consumption when idle              | Implement scheduled sleep/wake; GPU nodes don't need to run 24/7                                    |
| Ollama model storage on GPU node         | Use `hcloud-volumes` (Synology iSCSI) for PVC so models persist across node restarts                |

## Future Enhancements (not in scope)

- On-demand WoL triggered by pending GPU pod (requires custom controller/webhook)
- Multi-GPU model parallelism across both nodes (vLLM or similar)
- GPU monitoring in Prometheus/Grafana (ROCm exporter)
- Automatic GPU node suspend after idle timeout
- Additional GPU workloads: Stable Diffusion, Whisper transcription
