---
name: homelab-onboarding
description: >-
  End-to-end onboarding of new applications into the K3s homelab cluster. Creates all Kubernetes
  manifests (Kustomize), ArgoCD Application CRDs, HTTPRoutes, ExternalSecrets, Crossplane
  AppDBClaim database provisioning, PVCs, ConfigMaps, and optionally scaffolds the app repository
  with Dockerfiles and GitHub Actions CI/CD. Use this skill whenever someone wants to add a new
  app, deploy a new service, onboard an application, set up a new project in the homelab, or
  create Kubernetes manifests for the homelab cluster. Also use when the user mentions "new app",
  "deploy app", "add service to homelab", "onboard", or wants to scaffold any part of the homelab
  app structure.
---

# Homelab App Onboarding

This skill walks through the complete process of onboarding a new application into the homelab K3s
cluster. It gathers requirements, then generates all files in one pass.

The homelab uses a GitOps flow: push to Git → ArgoCD syncs to the K3s cluster. Every app follows
the "App of Apps" pattern where `apps/root.yaml` points to `apps/_argocd/`, which contains ArgoCD
Application CRDs that each point to `apps/<app-name>/`.

## Workflow

### Phase 1: Gather Requirements

Ask the user the following questions **one at a time** using the `ask_user` tool. Skip questions
that aren't relevant based on earlier answers.

1. **App name** — kebab-case identifier (e.g., `my-app`). This becomes the namespace, directory
   names, and resource names throughout.

2. **App description** — one sentence about what the app does.

3. **Services** — what containers/services does the app need?
   - Typical patterns: backend-only, frontend+backend, single container
   - For each service, ask: name, container image (or if they need a new one built), port

4. **Exposure** — how should the app be accessible?
   - Home only (`*.home.timosur.com`, HTTP, LAN) — most apps use this
   - Internet-facing (`*.timosur.com`, HTTPS with cert-manager)
   - Both home and internet
   - Not exposed (background worker / internal only)

5. **Custom domain** — does it need a domain other than `*.timosur.com` or `*.home.timosur.com`?
   If yes, capture the domain name — this requires adding listeners to the internet gateway.

6. **Database** — does the app need PostgreSQL?
   - If yes: database name (default: `app`), role name (default: same as app name)
   - Does the app need any PostgreSQL extensions? (e.g., `vector` for pgvector)
   - The database is provisioned on the central shared CNPG cluster via Crossplane AppDBClaim

7. **Secrets** — does the app need secrets from Azure Key Vault?
   - If yes: list of secret keys the app needs (e.g., `API_KEY`, `DATABASE_PASSWORD`)
   - Each will be stored in Azure Key Vault as `<app-name>-<secret-key-kebab>`

8. **Persistent storage** — does the app need a PVC?
   - If yes: storage size, storage class (`hcloud-volumes` default, or `storage-box-smb` for
     large shared data)

9. **Environment variables** — any non-secret config the app needs (ConfigMap)?
   - `TZ: Europe/Berlin` is always included by default

10. **App repo scaffolding** — should we also create Dockerfiles and GitHub Actions CI/CD?
    - If yes: what language/framework for each service?
    - Supported templates: Python/FastAPI backend, Node.js backend, React/Vite frontend, static nginx

11. **CronJob** — does the app need scheduled tasks?
    - If yes: schedule (cron expression), what it does

### Phase 2: Confirm Plan

Before generating files, present a summary of what will be created:

```
## Onboarding Plan for <app-name>

### Homelab repo files:
- apps/<app-name>/namespace.yaml
- apps/<app-name>/deployment.yaml
- apps/<app-name>/service.yaml
- apps/<app-name>/kustomization.yaml
- apps/<app-name>/configmap.yaml          (if env vars needed)
- apps/<app-name>/external-secret.yaml    (if secrets needed)
- apps/<app-name>/appdb.yaml              (if database needed)
- apps/<app-name>/pvc.yaml                (if storage needed)
- apps/<app-name>/cronjob.yaml            (if cronjob needed)
- apps/_argocd/<app-name>-app.yaml
- networking/httproutes/home/<app-name>.yaml     (if home exposure)
- networking/httproutes/internet/<app-name>.yaml  (if internet exposure)

### Existing files to update:
- apps/_argocd/kustomization.yaml
- networking/httproutes/home/kustomization.yaml      (if home)
- networking/httproutes/internet/kustomization.yaml   (if internet)

### App repo files (if requested):
- <service>/Dockerfile
- .github/workflows/build-and-push-images.yml
- docker-compose.yml
```

Ask the user to confirm before proceeding.

### Phase 3: Generate Files

Generate all files following the templates and patterns below. Use the `create` tool for new files
and the `edit` tool to update existing kustomization files.

After generating all files, print a post-deployment checklist (see the Checklist section below).

---

## File Templates

Read `references/templates.md` for the complete set of file templates to use when generating
manifests. The templates contain placeholder variables in `<angle-brackets>` that you replace
with the actual values gathered in Phase 1.

When generating files, follow these critical rules:

### Image references

If the user provides an existing image with a tag/digest, use it as-is. If the image is from
`ghcr.io/timosur/*`, use the SHA-pinned format:

```
ghcr.io/timosur/<repo>/<service>:sha-<commit>@sha256:<digest>
```

If the user doesn't have an image yet (new app repo), use a placeholder that makes it clear it
needs updating:

```
ghcr.io/timosur/<app-name>/<service>:latest  # TODO: Pin with SHA after first build
```

### Deployment strategy

Use `strategy.type: Recreate` for any single-replica deployment that uses a PVC. This avoids
volume mount conflicts during rolling updates.

### Probes

Every container must have `livenessProbe` and `readinessProbe`. Defaults:
- HTTP GET to `/health` (or `/` for frontend/nginx containers)
- `initialDelaySeconds: 30` / `periodSeconds: 30` for liveness
- `initialDelaySeconds: 10` / `periodSeconds: 10` for readiness

### Resources

Every container must have `resources.requests` and `resources.limits`. Sensible defaults:
- Backend: requests `128Mi`/`100m`, limits `512Mi`/`500m`
- Frontend (nginx): requests `64Mi`/`50m`, limits `256Mi`/`250m`

### Namespace labels

Internet-facing namespaces must include `exposure: internet` label. This is applied in
`namespace.yaml` metadata.labels. The Cilium network policies use this label to restrict LAN
access from internet-exposed pods.

### ExternalSecret naming

Azure Key Vault keys follow the pattern `<app-name>-<secret-key-kebab>`. For example, if the
app is `my-app` and needs `API_KEY`, the Azure KV key is `my-app-api-key`.

PostgreSQL credentials are **NOT** stored in Azure Key Vault. They are automatically provisioned
by Crossplane via the `AppDBClaim` resource, which creates a `<app-name>-db-connection` secret
in the app's namespace with keys: `host`, `port`, `username`, `password`, `dbname`, `sslmode`,
`uri`.

### Database provisioning (Crossplane AppDBClaim)

All new apps use the central shared CNPG cluster (`central-postgres` in namespace `postgres`)
via Crossplane self-service provisioning. Create an `appdb.yaml` with an `AppDBClaim` resource.
Crossplane automatically provisions the PostgreSQL role, database, optional extensions, and a
connection secret (`<app-name>-db-connection`) in the app's namespace.

The deployment should reference the Crossplane-managed secret for DB credentials:
- Individual `env` entries with `secretKeyRef` pointing to `<app-name>-db-connection`
- DB host in the ConfigMap: `central-postgres-rw.postgres.svc.cluster.local`

See the n8n app (`apps/n8n/`) as the reference implementation.

### Kustomization updates

When adding entries to existing `kustomization.yaml` files, add the new entry in alphabetical
order among the existing entries to keep things tidy.

### HTTPRoute naming

- Home routes: `<app-name>-home` in namespace `<app-name>`
- Internet routes: `<app-name>` in namespace `<app-name>`

---

## Post-Generation Checklist

After generating all files, remind the user of manual steps they still need to do:

```
## Manual Steps Required

### Before deploying:
- [ ] Add secrets to Azure Key Vault (key vault: homelab-timosur)
      Keys to add: <list the Azure KV key names>
      (Note: DB credentials are NOT needed in AKV — Crossplane provisions them automatically)
- [ ] Build and push Docker images (if new app repo)
- [ ] Update image SHA digests in deployment.yaml once images are built

### After deploying (ArgoCD will sync automatically on git push):
- [ ] Verify ArgoCD shows the app as synced and healthy
- [ ] Verify AppDBClaim is ready (if database): kubectl get appdbclaim -n <app-name>
- [ ] Verify connection secret exists (if database): kubectl get secret <app-name>-db-connection -n <app-name>
- [ ] Verify pods are running: kubectl get pods -n <app-name>
- [ ] Test the URL: <app-url>
- [ ] Verify database connection (if applicable)
- [ ] Check that Renovate detects new GHCR images (if applicable)
```

If the app uses a custom domain (not `*.timosur.com`), also remind:
```
- [ ] Add DNS records for <custom-domain> pointing to the cluster
- [ ] Add listener + certificate to networking/gateways/internet/gateway.yaml
```

---

## App Repo Scaffolding

When the user requests app repo scaffolding, generate files in a directory the user specifies
(or suggest `../<app-name>/` relative to the homelab repo). The scaffolding includes:

### Dockerfile templates

See `references/templates.md` for Dockerfile templates per framework.

### GitHub Actions

Generate `.github/workflows/build-and-push-images.yml` with:
- Trigger on push/PR to main + workflow_dispatch
- Multi-arch build: `linux/amd64,linux/arm64` (the cluster has ARM worker nodes)
- Push to `ghcr.io/timosur/<app-name>/<service>`
- Tags: `sha-<commit>`, `latest` on default branch

### docker-compose.yml

Generate a `docker-compose.yml` for local development that mirrors the K8s architecture:
- All services the app needs
- A PostgreSQL container if the app uses a database
- Volumes for persistent data
- Port mappings for local access
