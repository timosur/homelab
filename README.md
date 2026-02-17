# homelab

My HomeLab running on a local K3s cluster with GitOps workflow.

## Architecture

- **Cluster**: K3s on local hardware (provisioned via Ansible)
- **CNI**: Cilium with Gateway API support
- **GitOps**: ArgoCD with automated sync
- **Ingress**: Envoy Gateway (Gateway API)
- **TLS**: cert-manager with Let's Encrypt (DNS-01 via Cloudflare)
- **DNS**: External DNS with Cloudflare
- **Storage**: Synology CSI Driver + SMB CSI Driver
- **Secrets**: External Secrets Operator with Azure Key Vault

## Repository Structure

```text
homelab/
├── ansible/                    # Ansible playbooks for cluster provisioning
│   ├── inventory.yml
│   └── playbooks/
├── apps/                       # Application definitions and ArgoCD apps
│   ├── _argocd/               # ArgoCD Application manifests
│   ├── root.yaml              # Root ArgoCD Application (App of Apps)
│   ├── actual/                # Budget management
│   ├── cert-manager/          # Certificate management (DNS-01)
│   ├── cloudnative-pg/        # PostgreSQL operator
│   ├── external-secrets/      # External secrets operator
│   ├── garden/                # Garden management app
│   ├── givgroov/              # Music sharing app
│   ├── home-assistant/        # Home automation
│   ├── mealie/                # Recipe management
│   ├── open-webui/            # LLM interface
│   ├── paperless/             # Document management
│   ├── pi-hole/               # DNS ad blocker
│   ├── portfolio/             # Personal portfolio
│   ├── smb-csi-driver/        # SMB storage driver
│   ├── synology-csi-driver/   # Synology NAS storage driver
│   └── vinyl-manager/         # Vinyl collection manager
├── networking/                 # Networking configurations
│   ├── cilium-lb-ipam/        # Cilium LB IP address management
│   ├── cilium-network-policies/ # Network policies
│   ├── gateways/              # Gateway definitions
│   ├── httproutes/            # HTTPRoute definitions
│   └── kustomization.yaml     # Networking kustomization
├── keys/                       # SSH keys for cluster access
└── scripts/                    # Utility scripts
```

## Deployment Flow

### 1. Infrastructure Provisioning

The cluster is provisioned on local hardware using Ansible:

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/argocd-gitops-setup.yml
```

### 2. GitOps Bootstrap

```bash
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

- **cert-manager**: Automatic TLS certificate management with Let's Encrypt (DNS-01 via Cloudflare)
- **cloudnative-pg**: PostgreSQL operator for database management
- **external-secrets**: Secure secrets management from Azure Key Vault
- **smb-csi-driver**: SMB storage driver for NAS/storage box access
- **synology-csi-driver**: Synology NAS iSCSI storage driver

### User Applications

- **actual**: Budget and financial management
- **garden**: Garden management and planning application
- **givgroov**: Music sharing platform
- **home-assistant**: Home automation
- **mealie**: Recipe management and meal planning
- **open-webui**: Modern interface for Large Language Models
- **paperless**: Document management system
- **pi-hole**: DNS-level ad blocking
- **portfolio**: Personal portfolio website
- **vinyl-manager**: Vinyl record collection manager

All applications are accessible via `https://<app>.home.timosur.com`.

## Networking

- **Gateway**: Envoy Gateway (Gateway API) handles all ingress traffic
- **Load Balancer**: Cilium LB IPAM with L2 announcements
- **DNS**: External DNS automatically manages Cloudflare DNS records
- **TLS**: Wildcard and individual certificates via cert-manager + Let's Encrypt (DNS-01)
- **CNI**: Cilium
- **Network Policies**: Cilium-based network policies for isolation

### Available Services

- **ArgoCD**: `https://argo.home.timosur.com`
- **Applications**: `https://<app-name>.home.timosur.com`

## Key Features

- **GitOps Workflow**: All changes deployed via Git commits
- **Automated TLS**: Certificates automatically provisioned and renewed via DNS-01
- **Security**: Network policies and secret management via Azure Key Vault
- **Monitoring**: Hubble UI for network observability

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
