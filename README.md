# homelab

My HomeLab running on a local K3s cluster with GitOps workflow.

## Architecture

- **Cluster**: K3s on local hardware (1 control plane + 2 workers, provisioned via Ansible)
- **CNI**: Cilium with Gateway API support and L2 announcements
- **GitOps**: ArgoCD with automated sync (App of Apps pattern)
- **Ingress**: Two Envoy Gateway instances (home/LAN + internet)
- **TLS**: cert-manager with Let's Encrypt (DNS-01 via Cloudflare) for internet services
- **DNS**: UDR DynDNS updates Cloudflare records for `*.timosur.com`, `timosur.com`, `givgroov.de`; Pi-hole for local DNS
- **Storage**: Synology CSI Driver (iSCSI) + SMB CSI Driver
- **Secrets**: External Secrets Operator with Azure Key Vault
- **Dependency Updates**: Renovate Bot for automated image and chart updates

## Repository Structure

```text
homelab/
├── ansible/                    # Ansible playbooks and roles for cluster provisioning
│   ├── inventory.yml
│   ├── playbooks/
│   │   ├── argocd-gitops-setup.yml
│   │   ├── cluster-backup.yml
│   │   ├── k3s-cluster.yml
│   │   ├── k3s-update.yml
│   │   └── node-hardening.yml
│   └── roles/
│       ├── argocd-install/
│       ├── cilium/
│       ├── cluster-backup/
│       ├── gitops-setup/
│       ├── gpu-blacklist/
│       ├── k3s-control-plane/
│       ├── k3s-update/
│       ├── k3s-worker/
│       └── node-hardening/
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
│   ├── kustomization.yaml
│   ├── cilium-lb-ipam/        # IP pools and L2 announcements
│   ├── cilium-network-policies/ # Network segmentation (home + internet)
│   ├── gateways/              # Home and internet gateway definitions
│   │   ├── home/              #   *.home.timosur.com (HTTP, LAN only)
│   │   └── internet/          #   *.timosur.com (HTTPS, public)
│   └── httproutes/            # HTTPRoute definitions
│       ├── home/              #   LAN-only service routes
│       └── internet/          #   Public service routes
├── backup/                     # Backup data
├── keys/                       # SSH keys for cluster access
├── scripts/                    # Utility scripts
├── renovate.json               # Renovate Bot configuration
├── NETWORKING.md               # Detailed networking documentation
├── ONBOARDING_GUIDE.md         # Guide for adding new applications
└── RENOVATE.md                 # Renovate Bot documentation
```

## Deployment Flow

### 1. Infrastructure Provisioning

The cluster is provisioned on local hardware using Ansible:

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/k3s-cluster.yml
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

- **envoy-gateway**: Ingress controllers for home and internet traffic (Helm chart)
- **cert-manager**: Automatic TLS certificate management with Let's Encrypt (DNS-01 via Cloudflare)
- **cloudnative-pg**: PostgreSQL operator for database management
- **external-secrets**: Secure secrets management from Azure Key Vault
- **smb-csi-driver**: SMB storage driver for NAS/storage box access
- **synology-csi-driver**: Synology NAS iSCSI storage driver

### User Applications

| Application | Description | URL |
|---|---|---|
| **actual** | Budget and financial management | `finance.home.timosur.com` |
| **garden** | Garden management and planning | `garden.home.timosur.com` |
| **givgroov** | Music sharing platform | `givgroov.de` |
| **home-assistant** | Home automation | `ha.home.timosur.com` |
| **mealie** | Recipe management and meal planning | `mealie.timosur.com` |
| **open-webui** | Modern interface for LLMs | `ai.timosur.com` |
| **paperless** | Document management system | `docs.home.timosur.com` |
| **pi-hole** | DNS-level ad blocking | `pihole.home.timosur.com` |
| **portfolio** | Personal portfolio website | `timosur.com` |
| **vinyl-manager** | Vinyl record collection manager | `vinyl.home.timosur.com` |

## Networking

The cluster runs two separate Envoy Gateway instances to segment home and internet traffic.

| Gateway | IP | Protocol | Domain | Purpose |
|---|---|---|---|---|
| Home | `192.168.2.100` | HTTP | `*.home.timosur.com` | LAN-only services |
| Internet | `192.168.2.254` | HTTPS | `*.timosur.com` | Public services |

- **Load Balancer**: Cilium LB IPAM with L2 announcements
- **DNS**: UDR DynDNS updates Cloudflare A records for `*.timosur.com`, `timosur.com`, `givgroov.de`
- **TLS**: Wildcard `*.timosur.com` + individual certs via cert-manager + Let's Encrypt (DNS-01)
- **Network Policies**: Cilium-based policies enforce LAN/internet segmentation

For detailed networking documentation, see [NETWORKING.md](NETWORKING.md).

## Key Features

- **GitOps Workflow**: All changes deployed via Git commits
- **Home/Internet Segmentation**: Separate gateways with Cilium network policies
- **Automated TLS**: Certificates automatically provisioned and renewed via DNS-01
- **Security**: Network policies and secret management via Azure Key Vault
- **Automated Updates**: Renovate Bot keeps container images and Helm charts up to date
- **Observability**: Hubble UI for network observability

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

# View Hubble flows
kubectl exec -n kube-system ds/cilium -- hubble observe

# Check LoadBalancer IPs
kubectl get svc -A | grep LoadBalancer
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
For Renovate Bot configuration details, see [RENOVATE.md](RENOVATE.md).
