# homelab

My HomeLab running on a local K3s cluster with GitOps workflow.

## Architecture

- **Cluster**: K3s on local hardware (1 control plane + 2 workers, provisioned via Ansible)
- **CNI**: Cilium with Gateway API support and L2 announcements
- **GitOps**: ArgoCD with automated sync (App of Apps pattern)
- **Ingress**: Envoy Gateway for home/LAN services; Pangolin and Newt for internet services
- **TLS**: cert-manager for the home gateway; Pangolin for internet services
- **DNS**: Pi-hole for local DNS; public domains configured for Pangolin
- **Storage**: Synology CSI Driver (iSCSI), SMB CSI Driver, and NFS CSI Driver (Dark Factory workspaces)
- **Secrets**: External Secrets Operator with Azure Key Vault
- **Identity**: Authentik; Vaultwarden for passwords
- **Observability and Security**: Monitoring stack, Hubble, Tetragon, Trivy Operator, Kured for node reboots
- **Databases**: CloudNativePG, with Crossplane-provisioned per-app databases
- **AI / Agents**: AgentGateway, Dark Factory sandboxes (Kata) with a wake-on-LAN proxy
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
│   ├── affine/                # Knowledge base / notes
│   ├── agent-sandbox/         # Agent sandbox controller
│   ├── agentgateway/          # LLM and MCP gateway
│   ├── argocd/                # ArgoCD self-management overlay
│   ├── atuin/, atuin-ai/      # Shell history sync and AI service
│   ├── authentik/             # Identity provider
│   ├── bike-weather*/         # Bike weather app (backend, auth, preview)
│   ├── cert-manager/          # Certificate management (DNS-01)
│   ├── cloudnative-pg/        # PostgreSQL operator
│   ├── crossplane/            # Crossplane app database composition
│   ├── dark-factory/          # Dark Factory chart values, runtime class, alerts
│   ├── dark-factory-wake/     # Sandbox admission webhooks and power interlock
│   ├── external-secrets/      # External secrets operator
│   ├── garden/                # Garden management app
│   ├── givgroov/              # Music sharing app
│   ├── home-assistant/        # Home automation
│   ├── house-builder/         # House builder app
│   ├── kured/                 # Node reboot daemon
│   ├── mealie/                # Recipe management
│   ├── monitoring/            # Monitoring and alerting
│   ├── nfs-csi-driver/        # NFS storage driver (workspaces)
│   ├── open-webui/            # LLM interface
│   ├── pangolin-newt/         # Pangolin tunnel and public route reconciliation
│   ├── paperless/             # Document management
│   ├── penpot/                # Design and prototyping
│   ├── pi-hole/               # DNS ad blocker
│   ├── portfolio/             # Personal portfolio
│   ├── postgres/              # Shared PostgreSQL clusters
│   ├── smb-csi-driver/        # SMB storage driver
│   ├── synology-csi-driver/   # Synology NAS storage driver
│   ├── tetragon/              # Runtime security observability
│   ├── trivy-operator/        # Vulnerability scanning
│   ├── vaultwarden/           # Password manager
│   ├── vinyl-manager/         # Vinyl collection manager
│   └── wol-proxy/             # Wake-on-LAN proxy for the GPU/desktop node
├── networking/                 # Networking configurations
│   ├── kustomization.yaml
│   ├── cilium-lb-ipam/        # IP pools and L2 announcements
│   ├── cilium-network-policies/ # Network segmentation (home + internet)
│   ├── gateways/              # Home gateway definitions
│   │   └── home/              #   *.home.timosur.com (HTTPS, LAN only)
│   └── httproutes/            # HTTPRoute definitions
│       └── home/              #   LAN-only service routes
├── docs/                       # Docs, plans and specs (e.g. dark-factory-onboarding.md)
├── images/                     # Container images (dark-factory-project)
├── openspec/                   # OpenSpec specs and change records
├── scripts/                    # Utility scripts
├── services/                   # Custom services (wol-proxy)
├── models.yaml                 # Model definitions
├── renovate.json               # Renovate Bot configuration
├── AGENTS.md                   # Agent guidance (commit conventions)
└── TODO.md
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

### ArgoCD self-management and upgrades

ArgoCD manages its own installation through `apps/_argocd/argocd-app.yaml`.
The upstream release and local configuration are pinned in
`apps/argocd/kustomization.yml`. Renovate opens a reviewable PR for version
updates; after a PR is merged, ArgoCD automatically syncs the updated overlay.
Review and merge minor-version upgrades in order, reading each upstream upgrade
guide and backing up ArgoCD before merging. Renovate proposes separate minor
versions, but reviewers must ensure that no minor version is skipped.
Automatic pruning is disabled for this application to protect its CRDs and
other resources; review and remove obsolete resources separately when needed.

Ansible bootstraps the same overlay only when both the ArgoCD server deployment
and self-management Application are absent. Normal reruns do not overwrite a
self-managed installation. For recovery, `-e argocd_bootstrap_force=true`
explicitly reapplies the overlay using server-side apply with ArgoCD's
`argocd-controller` field manager and takes ownership of conflicting fields.
The overlay requires the `argocd` namespace and pins workloads to `homelab-amd`.

Older bootstraps used other field managers, including `argocd-bootstrap` and
update-based Ansible managers. Matching values can remain shared with those
managers even after ArgoCD syncs, preventing later field removals from taking
effect. For existing installations, inspect `metadata.managedFields`, back up
the affected objects, and carefully relinquish obsolete ownership before
depending on field removal. Do not remove entries belonging to active
controllers or clear all managed fields indiscriminately.

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

- **envoy-gateway**: Ingress controller for home traffic (Helm chart)
- **pangolin-newt**: Tunnel and route reconciliation for internet services
- **cert-manager**: Automatic TLS certificate management with Let's Encrypt (DNS-01 via Cloudflare)
- **cloudnative-pg**: PostgreSQL operator for database management
- **external-secrets**: Secure secrets management from Azure Key Vault
- **smb-csi-driver**: SMB storage driver for NAS/storage box access
- **synology-csi-driver**: Synology NAS iSCSI storage driver
- **nfs-csi-driver**: NFS storage driver for Dark Factory workspaces
- **argocd**: Self-managed ArgoCD installation
- **agentgateway**: LLM/MCP gateway (MCP endpoints require authentication)
- **crossplane**: Provisions per-application databases (`appdb`)
- **monitoring, tetragon, trivy-operator, kured**: Observability, runtime security, vulnerability scanning, node reboots
- **authentik, vaultwarden**: Identity and password management

### Dark Factory

Dark Factory runs autonomous agent workspaces in Kata sandboxes. `wol-proxy`
(`services/wol-proxy`) wakes the desktop on demand and enforces workspace
ownership and admission; `dark-factory-wake` provides the admission webhooks and
the power interlock for automatic sleep. The ArgoCD apps for it require manual
sync, and automatic sleep is not yet enabled. See
[docs/dark-factory-onboarding.md](docs/dark-factory-onboarding.md) before syncing.

### Coder development workspaces

Personal development workspaces are separate from Dark Factory's Kata sandboxes. From the home
network, sign in at [coder.home.timosur.com](https://coder.home.timosur.com/) with Authentik and
authorize GitHub if prompted. Select the existing `homelab` workspace, or create one using the
`board-member` template.

With the Coder CLI and Zed installed, use a separate CLI and SSH configuration to avoid replacing
another Coder login:

```bash
mkdir -p "$HOME/.config/coder-homelab" "$HOME/.ssh"
chmod 700 "$HOME/.config/coder-homelab"
coder --global-config "$HOME/.config/coder-homelab" login https://coder.home.timosur.com
coder --global-config "$HOME/.config/coder-homelab" list
coder --global-config "$HOME/.config/coder-homelab" start homelab
coder --global-config "$HOME/.config/coder-homelab" config-ssh \
  --ssh-config-file "$HOME/.ssh/coder-homelab.conf" --ssh-host-prefix coder-home.
```

The separate CLI directory stores a session token in a file; keep it private and do not share it.
In Zed, choose **Remote Projects → Connect New Server** and enter
`ssh -F /absolute/path/to/.ssh/coder-homelab.conf coder-home.homelab`, replacing the path with
your generated SSH config. Open `/home/coder` or a project beneath it. Replace `homelab` with your
workspace name in the commands if different.

Stop a workspace from Coder or run
`coder --global-config "$HOME/.config/coder-homelab" stop homelab`. It also stops after 30 minutes
without detected activity; an open SSH or Zed session can keep it active. `/home/coder` persists,
but APT packages and other changes outside it do not survive a rebuild.

Cilium blocks direct access to the private LAN, nodes, Kubernetes API and unrelated cluster
services; public internet, DNS and Coder remain available. Tetragon adds runtime defenses, but
these shared-kernel pods are not VM isolation. The home public IP remains reachable, including
any UniFi management interface exposed there.

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
| **penpot** | Design and prototyping | `penpot.home.timosur.com` |
| **pi-hole** | DNS-level ad blocking | `pihole.home.timosur.com` |
| **portfolio** | Personal portfolio website | `timosur.com` |
| **vinyl-manager** | Vinyl record collection manager | `vinyl.home.timosur.com` |

## Networking

The home gateway handles LAN traffic. Pangolin exposes internet services through Newt;
public routes are configured in `apps/pangolin-newt/routes.json`.

| Exposure | IP | Protocol | Domain | Purpose |
|---|---|---|---|---|
| Home gateway | `192.168.2.100` | HTTPS | `*.home.timosur.com` | LAN-only services |
| Pangolin | — | HTTPS | Configured public domains | Internet services |

- **Load Balancer**: Cilium LB IPAM with L2 announcements
- **DNS**: Pi-hole for local names; configure public domains for Pangolin
- **TLS**: cert-manager for the home gateway; Pangolin for public routes
- **Network Policies**: Cilium-based policies enforce LAN/internet segmentation

## Key Features

- **GitOps Workflow**: All changes deployed via Git commits
- **Home/Internet Segmentation**: Home gateway and Pangolin with Cilium network policies
- **Automated TLS**: Home gateway certificates provisioned and renewed via DNS-01
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

Renovate Bot is configured in `renovate.json`. Agents should follow [AGENTS.md](AGENTS.md) for commits.
