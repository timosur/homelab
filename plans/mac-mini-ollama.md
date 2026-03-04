# Plan: Add Mac Mini with Ollama to Homelab

## Problem Statement

Add a Mac Mini M4 (16GB) at `192.168.2.50` to the homelab to run Ollama natively on macOS, leveraging Apple Silicon Metal GPU acceleration for ~5-10x faster LLM inference (medium models like Llama 3.1 8B, Mistral 7B). The Mac Mini will NOT join the K3s cluster — instead, Ollama runs natively on macOS and the existing K8s open-webui app is updated to point to it.

## Approach

1. **Ansible**: Add Mac Mini to inventory and create a playbook/role to install & configure Ollama as a launchd service
2. **Kubernetes**: Replace the in-cluster Ollama StatefulSet with a headless Service + Endpoints pointing to the Mac Mini's native Ollama
3. **Cleanup**: Remove the old Ollama StatefulSet and its PVC template from the cluster

## Architecture

```
┌─────────────────────────────────┐     ┌──────────────────────────┐
│  K3s Cluster                    │     │  Mac Mini M4 (native)    │
│                                 │     │  192.168.2.50            │
│  open-webui ──► ollama-service ─┼────►│  Ollama :11434           │
│  (Deployment)   (Endpoints)     │     │  (Metal GPU accelerated) │
│                                 │     │  Managed by launchd      │
└─────────────────────────────────┘     └──────────────────────────┘
```

## Todos

### 1. `ansible-mac-mini-inventory` — Add Mac Mini to Ansible inventory
- Add a new host group `mac_mini` (outside `k3s_cluster`) in `ansible/inventory.yml`
- Host: `mac-mini`, IP: `192.168.2.50`, connection: `ssh`

### 2. `ansible-ollama-role` — Create Ansible role for Ollama on macOS
- Create `ansible/roles/ollama-macos/tasks/main.yml`
- Tasks:
  - Install Ollama via the official install script (`curl -fsSL https://ollama.com/install.sh | sh`) or check if it's already installed
  - Create a launchd plist at `~/Library/LaunchAgents/com.ollama.serve.plist` to auto-start Ollama on boot
  - Set `OLLAMA_HOST=0.0.0.0` so it binds to all interfaces (accessible from K3s cluster)
  - Set `OLLAMA_ORIGINS=*` to allow cross-origin requests from open-webui
  - Load the launchd agent
  - Verify Ollama is running and accessible

### 3. `ansible-ollama-playbook` — Create playbook for Mac Mini setup
- Create `ansible/playbooks/mac-mini-ollama.yml`
- Target the `mac_mini` host group
- Apply the `ollama-macos` role

### 4. `k8s-ollama-service-endpoints` — Replace Ollama Service with external Endpoints
- Modify `apps/open-webui/ollama-service.yaml`:
  - Remove the `selector` (so it doesn't target pods)
  - Create a matching `Endpoints` resource pointing to `192.168.2.50:11434`
- This makes the existing `ollama-service.open-webui.svc.cluster.local:11434` URL resolve to the Mac Mini — **no changes needed to open-webui deployment env vars**

### 5. `k8s-remove-ollama-statefulset` — Remove in-cluster Ollama StatefulSet
- Delete `apps/open-webui/ollama-statefulset.yaml`
- Remove `ollama-statefulset.yaml` from `apps/open-webui/kustomization.yaml`
- Add new `ollama-endpoints.yaml` to kustomization if Endpoints are in a separate file

### 6. `docs-update` — Update documentation
- Update `README.md` to mention the Mac Mini and its role
- Note in relevant docs that Ollama runs natively on macOS for GPU acceleration

## Key Decisions

- **No K3s on Mac Mini**: K3s is Linux-only; running in a VM would lose Metal GPU acceleration
- **Service + Endpoints pattern**: The standard K8s approach for external services — keeps open-webui config unchanged since the service name stays the same
- **launchd (not Homebrew service)**: Direct launchd plist gives full control over environment variables and startup behavior
- **OLLAMA_HOST=0.0.0.0**: Required to make Ollama accessible from the LAN/cluster (default is localhost only)
- **No firewall changes needed**: Mac Mini is on the same LAN as the cluster nodes

## Risks & Considerations

- **Mac Mini availability**: If the Mac Mini is powered off or Ollama crashes, open-webui will show Ollama as unavailable. Consider adding a health check or monitoring.
- **No automatic model pulling**: Models need to be pulled manually on the Mac Mini after setup (`ollama pull llama3.1:8b`)
- **Storage**: 16GB RAM means ~10-12GB available for models after macOS + Ollama overhead. Llama 3.1 8B (Q4 quantized) uses ~4.7GB, leaving room for 1-2 loaded models.
- **Network dependency**: Inference latency adds ~1ms network hop vs in-cluster, which is negligible
