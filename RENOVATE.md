# Renovate Bot Configuration

This repository is configured with Renovate Bot to automatically manage dependency updates across the entire homelab infrastructure.

## What Renovate Manages

### 🐳 Container Images

- **Kubernetes Deployments**: All Docker images in `apps/*/deployment.yaml`
- **Init Containers**: busybox, alpine, and other utility images
- **Application Images**: n8n, mealie, seafile, zipline, portfolio, etc.
- **Custom Images**: Your GHCR images with SHA or tag-based versioning

### 🏗️ Infrastructure as Code

- **Terraform Modules**: kube-hetzner module versions
- **Terraform Providers**: Hetzner Cloud provider versions
- **Crossplane Providers**: Upbound provider packages

### ⚙️ Kubernetes Ecosystem

- **Helm Charts**: External-secrets, Crossplane via ArgoCD
- **ArgoCD Applications**: Chart versions and target revisions
- **External DNS**: Container image versions
- **Cilium**: Version management in Terraform configuration

### 🔧 CI/CD Dependencies

- **GitHub Actions**: All action versions in workflows
- **Runner Images**: Ubuntu, setup actions, etc.

## Configuration Files

### Primary Configuration

- `renovate.json` - Main Renovate configuration
- `.github/renovate.json` - GitHub-specific overrides

### Automation

- `.github/workflows/renovate-auto-approve.yaml` - Auto-approval for safe updates

## Update Policies

### 🚀 Auto-merged Updates

- **Patch/Minor**: busybox, redis, memcached, mariadb
- **GitHub Actions**: All action version updates
- **Digest Updates**: SHA-based image updates

### 👀 Manual Review Required

- **Major Updates**: All major version bumps
- **Custom Images**: Your GHCR images tagged with `:latest` or `:main`
- **Terraform Modules**: Infrastructure-critical updates
- **Helm Charts**: Kubernetes ecosystem components

### 📅 Update Schedule

- **Regular Updates**: Monday mornings before 6 AM (Europe/Zurich)
- **Security Updates**: Immediate (vulnerability alerts enabled)
- **Development Images**: Real-time monitoring for `:latest` tags

## Package Rules & Grouping

### Grouped Updates

- **Kubernetes Ecosystem**: cert-manager, external-dns, argocd
- **Terraform**: All Terraform-related dependencies
- **GitHub Actions**: All CI/CD action updates

### Special Handling

- **Pin Digests**: Custom images get digest pinning
- **Version Comments**: Kube-hetzner version in comments
- **SHA References**: Custom handling for SHA-based tags

## Security

### Vulnerability Management

- **OSV Alerts**: Enabled for all package types
- **GitHub Security Advisories**: Automatic vulnerability detection
- **Priority Updates**: Security patches get immediate attention

### Safe Defaults

- **Branch Protection**: All updates via pull requests
- **Validation**: Kubernetes manifest and Terraform validation
- **Approval Required**: Manual review for critical infrastructure

## Usage

### Enable Renovate

1. Install [Renovate GitHub App](https://github.com/apps/renovate) on your repository
2. Configure repository access for `timosur/homelab`
3. Renovate will automatically start monitoring and creating PRs

### Customize Behavior

Edit `renovate.json` to modify:

- Update schedules
- Auto-merge policies
- Package groupings
- Ignore patterns

### Monitor Updates

- **Dependency Dashboard**: Check Issues tab for Renovate dashboard
- **PR Labels**: All PRs tagged with `renovate` label
- **Assignee**: Updates assigned to `timosur`

## Troubleshooting

### Common Issues

1. **Missing Updates**: Check file patterns in `fileMatch` arrays
2. **Failed Validation**: Review GitHub Actions logs for validation errors
3. **Auto-merge Not Working**: Verify branch protection rules

### Debug Configuration

```bash
# Validate Renovate config locally
npx renovate-config-validator renovate.json
```

### Manual Trigger

Comment `@renovate rebase` on any PR to trigger Renovate actions.

## File Patterns Monitored

```
apps/**/*.yaml              # Kubernetes manifests
apps/_argocd/*.yaml         # ArgoCD applications
networking/**/*.yaml        # Networking configurations
infrastructure/*.tf         # Terraform files
.github/workflows/*.yaml    # GitHub Actions
```

## Custom Managers

Renovate uses custom regex managers for:

- Crossplane provider packages
- Kube-hetzner version comments
- Cilium version in Terraform
- SHA-based container image references
- PostgreSQL versions in CNPG manifests

---

_This configuration ensures your homelab stays secure and up-to-date with minimal manual intervention while maintaining control over critical infrastructure changes._
