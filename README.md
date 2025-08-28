# homelab

My HomeLab running on Hetzner using Kube Hetzner K8s cluster and GitOps workflow

## Architecture

- **Cluster**: K3s on Hetzner Cloud (1 control plane + 2 workers)
- **CNI**: Cilium with Gateway API support
- **GitOps**: ArgoCD
- **Ingress**: Cilium Gateway API
- **TLS**: cert-manager with Let's Encrypt

## Repository Structure

```text
homelab/
├── infrastructure/          # Terraform for cluster provisioning
├── bootstrap/              # Initial cluster setup manifests
│   ├── argocd/            # ArgoCD installation
│   └── gateway-api/       # Gateway API CRDs and GatewayClass
├── apps/                  # ArgoCD Applications for workloads
│   ├── argocd/           # ArgoCD networking configuration
│   └── cert-manager/     # Certificate management
├── infrastructure-apps/   # ArgoCD Applications for infrastructure
│   ├── cilium/          # Cilium configuration
│   └── gateway-api/     # Gateway API controllers
└── networking/           # Networking configurations
    ├── gateways/        # Gateway definitions
    ├── httproutes/      # HTTPRoute definitions
    └── certificates/    # Certificate definitions
```

## Deployment Flow

### 1. Infrastructure Deployment

```bash
cd infrastructure
terraform init
terraform plan
terraform apply
```

### 2. Bootstrap GitOps

```bash
# Get kubeconfig from Terraform output
terraform output -raw kubeconfig > ~/.kube/config-homelab
export KUBECONFIG=~/.kube/config-homelab

# Bootstrap the cluster
./bootstrap.sh
```

### 3. Access ArgoCD

```bash
# Port forward to ArgoCD
kubectl port-forward svc/argocd-server -n argocd 8080:443

# Get admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

### 4. Monitor Deployment

```bash
# Watch applications
kubectl get applications -n argocd -w

# Check application status
kubectl get httproutes,gateways,certificates -A
```

## Networking

- **Main Gateway**: `gateway-system/main-gateway` handles all traffic
- **ArgoCD Route**: Available at `https://argocd.timosur.com`
- **TLS**: Wildcard certificate for `*.timosur.com`
