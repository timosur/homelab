# homelab

My HomeLab running on Hetzner using Kube Hetzner K8s cluster and GitOps workflow

## Architecture

- **Cluster**: K3s on Hetzner Cloud (1 control plane + 3 workers: 2x AMD64 + 1x ARM64)
- **CNI**: Cilium v1.18.1 with Gateway API support and WireGuard encryption
- **GitOps**: ArgoCD with automated sync
- **Ingress**: Cilium Gateway API
- **TLS**: cert-manager with Let's Encrypt
- **State Management**: Terraform Cloud for remote state
- **Infrastructure**: Kube-Hetzner v2.18.1

## Repository Structure

```text
homelab/
├── infrastructure/              # Terraform for cluster provisioning
│   ├── backend.tf              # Terraform Cloud backend configuration
│   ├── kube.tf                 # Main Kube-Hetzner cluster definition
│   └── hcloud-microos-snapshots.pkr.hcl # Packer configuration
├── apps/                       # Application definitions and ArgoCD apps
│   ├── _argocd/               # ArgoCD Application manifests
│   ├── root.yaml              # Root ArgoCD Application (App of Apps)
│   ├── cert-manager/          # Certificate management
│   ├── cloudnative-pg/        # PostgreSQL operator
│   ├── crossplane/            # Cloud infrastructure management
│   ├── external-secrets/      # External secrets operator
│   ├── garden/                # Garden management app
│   ├── mealie/                # Recipe management
│   ├── n8n/                   # Workflow automation
│   ├── open-webui/            # LLM interface
│   ├── portfolio/             # Personal portfolio
│   ├── seafile/               # File sync and sharing
│   └── zipline/               # File upload service
├── networking/                 # Networking configurations
│   ├── gateways/              # Gateway definitions
│   ├── httproutes/            # HTTPRoute definitions
│   ├── external-dns/          # External DNS configuration
│   └── kustomization.yaml     # Networking kustomization
└── keys/                      # SSH keys for cluster access
```

## Deployment Flow

### 1. Infrastructure Deployment

The cluster uses Terraform Cloud for state management and Kube-Hetzner for provisioning:

```bash
cd infrastructure
terraform init
terraform plan
terraform apply
```

**Cluster Configuration:**

- 1x Control Plane: `cax21` (ARM64, 4 vCPU, 8GB RAM)
- 2x Workers: `cx32` (AMD64, 8 vCPU, 32GB RAM)
- 1x ARM Worker: `cax21` (ARM64, 4 vCPU, 8GB RAM)
- Load Balancer: `lb11` in fsn1
- Cilium with WireGuard encryption and Gateway API
- Cert-manager pre-installed

### 2. GitOps Bootstrap

After infrastructure deployment, the cluster automatically bootstraps with:

```bash
# Get kubeconfig from Terraform output
terraform output -raw kubeconfig > ~/.kube/config-homelab
export KUBECONFIG=~/.kube/config-homelab

# Apply root ArgoCD application (App of Apps pattern)
kubectl apply -f apps/root.yaml
```

The root application will automatically deploy all other applications defined in `apps/_argocd/`.

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

# Monitor cluster health
kubectl get nodes -o wide
kubectl get pods -A
```

## Applications

The homelab runs several applications managed through ArgoCD:

### Infrastructure Applications

- **cert-manager**: Automatic TLS certificate management with Let's Encrypt
- **cloudnative-pg**: PostgreSQL operator for database management
- **crossplane**: Cloud infrastructure management and provisioning
- **external-secrets**: Secure secrets management from external sources

### User Applications

- **garden**: Garden management and planning application
- **mealie**: Recipe management and meal planning
- **n8n**: Workflow automation and integration platform
- **open-webui**: Modern interface for Large Language Models
- **portfolio**: Personal portfolio website
- **seafile**: File synchronization and sharing platform
- **zipline**: Fast and reliable file upload service

All applications are accessible via `https://<app>.timosur.com` with automatic TLS certificates.

## Networking

- **Gateway**: Cilium Gateway API handles all ingress traffic
- **Load Balancer**: Hetzner Cloud Load Balancer (lb11) in fsn1
- **DNS**: External DNS automatically manages DNS records
- **TLS**: Wildcard and individual certificates via cert-manager + Let's Encrypt
- **CNI**: Cilium with WireGuard node-to-node encryption
- **Routing**: Tunnel mode for compatibility with Hetzner Cloud

### Available Services

- **ArgoCD**: `https://argocd.timosur.com`
- **Applications**: `https://<app-name>.timosur.com`

## Key Features

- **GitOps Workflow**: All changes deployed via Git commits
- **Automated TLS**: Certificates automatically provisioned and renewed
- **Multi-Architecture**: Supports both AMD64 and ARM64 workloads
- **High Availability**: Load balancer with multiple worker nodes
- **Security**: WireGuard encryption, network policies, and secret management
- **Monitoring**: Hubble UI for network observability
- **Scalability**: Easy horizontal scaling with additional worker nodes

## Useful Commands

### Cluster Management

```bash
# Get cluster status
kubectl get nodes -o wide
kubectl get pods -A

# Check ArgoCD applications
kubectl get applications -n argocd
kubectl describe application <app-name> -n argocd

# View application logs
kubectl logs -f deployment/<app-name> -n <namespace>

# Port forward for local access
kubectl port-forward svc/<service-name> -n <namespace> <local-port>:<service-port>
```

### Certificate Management

```bash
# Check certificates
kubectl get certificates -A
kubectl get certificaterequests -A

# Check cert-manager logs
kubectl logs -f deployment/cert-manager -n cert-manager
```

### Network Troubleshooting

```bash
# Check gateways and routes
kubectl get gateways -A
kubectl get httproutes -A

# Check Cilium status
kubectl exec -n kube-system ds/cilium -- cilium status
kubectl exec -n kube-system ds/cilium -- cilium connectivity test

# View Hubble flows
kubectl exec -n kube-system ds/cilium -- hubble observe
```

### Database Management

```bash
# Check PostgreSQL clusters
kubectl get postgresql -A
kubectl get pooler -A

# Connect to database
kubectl exec -it <postgresql-pod> -n <namespace> -- psql
```

## Troubleshooting

Common issues and solutions:

1. **Application not accessible**: Check HTTPRoute, Gateway, and Certificate status
2. **Certificate issues**: Verify cert-manager logs and ACME challenge records
3. **Database connection errors**: Check PostgreSQL cluster status and credentials
4. **ArgoCD sync failures**: Review application events and resource conflicts
5. **Network connectivity**: Use Cilium connectivity tests and Hubble observability

For detailed onboarding of new applications, see [ONBOARDING_GUIDE.md](ONBOARDING_GUIDE.md).
