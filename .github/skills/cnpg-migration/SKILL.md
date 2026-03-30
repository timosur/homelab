---
name: cnpg-migration
description: >-
  Migrate an application from its own per-app CloudNative-PG (CNPG) cluster to the central shared
  CNPG cluster using Crossplane AppDBClaim self-service provisioning. Use this skill when migrating
  any app's database to the central postgres cluster, switching an app to use Crossplane-managed
  database credentials, removing an old per-app CNPG Cluster CRD, or performing any "migrate
  database", "central postgres", "AppDBClaim", "cnpg migration", or "consolidate database" task.
  DO NOT USE FOR: creating the central cluster itself (already done), modifying the Crossplane
  composition/XRD, or general Kubernetes troubleshooting.
---

# CNPG Central Cluster Migration

Migrate a per-app CNPG Cluster to the central shared `central-postgres` cluster in the `postgres`
namespace using Crossplane `AppDBClaim` for self-service database provisioning.

## Architecture

- **Central cluster**: `central-postgres` in namespace `postgres`, CNPG Cluster with
  `enableSuperuserAccess: true`, storage on `hcloud-volumes`
- **Crossplane self-service**: Apps create an `AppDBClaim` (XRD: `k8s.homelab.timosur.com/v1`).
  Crossplane provisions a Role, Database, optional Extensions, and a connection Secret
  (`<appName>-db-connection`) in the app's namespace
- **Connection secret keys**: `host`, `port`, `username`, `password`, `dbname`, `sslmode`, `uri`
- **Central host**: `central-postgres-rw.postgres.svc.cluster.local`

## Workflow

### Phase 1: Research the App

Before making any changes, read and understand the app's current database wiring:

1. Read `apps/<app>/postgres.yaml` — note: database name, owner/role name, extensions, storage
2. Read `apps/<app>/kustomization.yaml` — note all resources listed
3. Read `apps/<app>/configmap*.yaml` — find DB host/port/database env vars
4. Read `apps/<app>/external-secret*.yaml` and `apps/<app>/secret*.yaml` — find DB credential
   wiring (username/password env vars, DATABASE_URL templates)
5. Read `apps/<app>/deployment*.yaml` — find how DB credentials are consumed:
   - `envFrom.secretRef` (whole secret mounted as env vars)
   - `envFrom.configMapRef` (whole configmap mounted)
   - `env[].valueFrom.secretKeyRef` (individual keys)
6. Classify the app into a **connection pattern** (see Connection Patterns below)

### Phase 2: Create the AppDBClaim

Create `apps/<app>/appdb.yaml`:

```yaml
apiVersion: k8s.homelab.timosur.com/v1
kind: AppDBClaim
metadata:
  name: <app>-db
  namespace: <app>
spec:
  appName: <app>
  databaseName: <database-name>
  roleName: <role-name>
  compositionRef:
    name: appdb-central-postgres
  # Only if the app needs extensions (e.g., open-webui needs pgvector):
  # extensions:
  #   - name: vector
```

**Naming rules**:

- `appName` **must** match the app's namespace (Crossplane creates the secret there)
- `databaseName`: use the same DB name as the old CNPG cluster's `bootstrap.initdb.database`
- `roleName`: use the same role name as the old CNPG cluster's `bootstrap.initdb.owner`
- Use underscores in role/database names if the old cluster did (e.g., `vinyl_manager`)

### Phase 3: Update App Configuration

Apply changes based on the app's connection pattern. The Crossplane-managed secret is always
named `<appName>-db-connection` with keys: `host`, `port`, `username`, `password`, `dbname`,
`sslmode`, `uri`.

#### Pattern A: ConfigMap host + Secret user/password (envFrom)

Apps that load DB host from ConfigMap and credentials from a Secret via `envFrom`.

**Examples**: mealie, paperless, bike-weather-auth, garden

Steps:

1. **ConfigMap**: Change the DB host value to `central-postgres-rw.postgres.svc.cluster.local`
2. **Deployment**: Replace the old `envFrom.secretRef` for postgres credentials with individual
   `env` entries reading from the Crossplane connection secret:
   ```yaml
   env:
     - name: <USERNAME_ENV_VAR>
       valueFrom:
         secretKeyRef:
           name: <app>-db-connection
           key: username
     - name: <PASSWORD_ENV_VAR>
       valueFrom:
         secretKeyRef:
           name: <app>-db-connection
           key: password
   ```
3. **ExternalSecret**: Remove the postgres credentials ExternalSecret entirely (or remove the
   postgres-related data entries if the ExternalSecret also contains non-DB secrets)

#### Pattern B: ConfigMap host + Secret password (individual env)

Apps that load DB host from ConfigMap and password from a Secret via individual `env` entries.

**Examples**: n8n (already migrated — reference implementation)

Steps:

1. **ConfigMap**: Change the DB host value to `central-postgres-rw.postgres.svc.cluster.local`
2. **Deployment**: Change `secretKeyRef` entries to read from `<app>-db-connection` secret
3. **ExternalSecret**: Remove postgres credential entries

#### Pattern C: ExternalSecret builds DATABASE_URL

Apps where the ExternalSecret templates a full `DATABASE_URL` string using AKV credentials.

**Examples**: bike-weather, bike-weather-preview, vinyl-manager, open-webui

Steps:

1. **ExternalSecret**: Replace the templated `DATABASE_URL` with a reference to the Crossplane
   connection secret's `uri` key. Two approaches:
   - **Option 1** (preferred): Remove DATABASE_URL from ExternalSecret entirely. Add an `env`
     entry in the Deployment reading `uri` from the Crossplane secret:
     ```yaml
     env:
       - name: DATABASE_URL
         valueFrom:
           secretKeyRef:
             name: <app>-db-connection
             key: uri
     ```
   - **Option 2**: If the URL format differs from standard `postgresql://` (e.g., uses
     `postgresql+asyncpg://`), keep it in ExternalSecret but source from the connection secret
     instead of AKV.
2. **ConfigMap**: If it has a separate `DB_HOST` entry, update to
   `central-postgres-rw.postgres.svc.cluster.local`
3. Remove AKV-sourced postgres credential entries from ExternalSecret

**IMPORTANT for non-standard URI schemes**: The Crossplane connection secret's `uri` key uses
`postgresql://` scheme. Apps requiring `postgresql+asyncpg://` (like bike-weather) need the
deployment to keep a template in the ExternalSecret that reads from the Crossplane secret

#### Pattern D: ArgoCD Helm values override

Apps where database connection is configured via ArgoCD Application Helm values (not plain manifests).

**Examples**: agents (kagent)

Steps:

1. Read `apps/_argocd/<app>-app.yaml` to find where DB host/credentials are configured
2. Update Helm value overrides to reference the Crossplane connection secret
3. Remove old postgres ExternalSecret

### Phase 4: Data Migration (if app has existing data)

**Skip this phase if the app's database is empty or disposable** (e.g., preview environments).

For apps with data that must be preserved:

1. **Scale down the app** to prevent writes:

   ```bash
   kubectl scale deployment/<app> -n <app> --replicas=0
   ```

2. **Run pg_dump/pg_restore** from the old cluster to the central cluster:

   ```bash
   # From a pod with psql access, or a temporary Job:
   pg_dump -h <old-cluster>-rw.<namespace>.svc.cluster.local \
           -U <old-owner> -d <database> --no-owner --no-acl | \
   psql -h central-postgres-rw.postgres.svc.cluster.local \
        -U <new-role> -d <database>
   ```

   The new role's password is in the `<app>-db-connection` secret (key: `password`).

3. **GRANT ownership** if needed — the Crossplane role is the owner of the new database, so
   tables created by pg_restore may need ownership transfer:

   ```sql
   REASSIGN OWNED BY <old_owner> TO <new_role>;
   ```

4. **Scale the app back up** and verify functionality.

### Phase 5: Cleanup

1. **Delete** `apps/<app>/postgres.yaml` (the old CNPG Cluster CRD)
2. **Delete** the postgres credentials ExternalSecret file if it was a standalone file
   (e.g., `postgres-external-secret.yaml`, `postgres-password-secret.yaml`)
3. **Update** `apps/<app>/kustomization.yaml`:
   - Remove `postgres.yaml` entry
   - Remove old postgres ExternalSecret entry (if separate file)
   - Add `appdb.yaml` entry
4. ArgoCD auto-sync with `prune: true` will delete the old CNPG Cluster and its PVC

### Phase 6: Verify

After ArgoCD syncs:

```bash
# Verify AppDBClaim is ready
kubectl get appdbclaim -n <app>

# Verify connection secret exists
kubectl get secret <app>-db-connection -n <app>

# Verify the database exists in central cluster
kubectl exec -n postgres central-postgres-1 -- psql -U postgres -c "\l" | grep <database>

# Verify the role exists
kubectl exec -n postgres central-postgres-1 -- psql -U postgres -c "\du" | grep <role>

# Verify the app is running and connected
kubectl get pods -n <app>
kubectl logs -n <app> deployment/<app> | tail -20
```

---

## Connection Patterns Reference

### Apps remaining to migrate

| App               | Pattern | DB Name       | Role          | Host Env Var                             | Credential Source                                                 | Special                  |
| ----------------- | ------- | ------------- | ------------- | ---------------------------------------- | ----------------------------------------------------------------- | ------------------------ |
| bike-weather-auth | A       | authentik     | authentik     | `AUTHENTIK_POSTGRESQL__HOST` (configmap) | ConfigMap has user, ExternalSecret has password                   | `jit=off` param          |
| vinyl-manager     | C       | vinyl_manager | vinyl_manager | N/A (URL only)                           | ExternalSecret templates `DATABASE_URL`                           | underscore names         |
| bike-weather      | C       | app           | app           | `DB_HOST` (configmap)                    | ExternalSecret templates `DATABASE_URL` (`postgresql+asyncpg://`) | asyncpg scheme           |
| garden            | A       | garden        | garden        | `DB_HOST` (configmap)                    | ExternalSecret `garden-postgres-password`                         | multi-service app        |
| mealie            | A       | mealie        | mealie        | `POSTGRES_SERVER` (configmap)            | ExternalSecret `mealie-postgres-password`                         | `max_connections=200`    |
| open-webui        | C       | openwebui     | webui         | N/A (URL only)                           | ExternalSecret templates `database-url` + `pgvector-db-url`       | needs `vector` extension |

### Completed migrations

| App                  | Status                 | Commit    |
| -------------------- | ---------------------- | --------- |
| n8n                  | Pattern B, scaled to 0 | `aa1c108` |
| agents               | Pattern D, Helm values | —         |
| paperless            | Pattern A              | —         |
| bike-weather-preview | Pattern C, scaled to 0 | —         |

---

## Reference Implementation: n8n

n8n was the first app migrated. Use it as a reference for the Pattern B flow.

### appdb.yaml

```yaml
apiVersion: k8s.homelab.timosur.com/v1
kind: AppDBClaim
metadata:
  name: n8n-db
  namespace: n8n
spec:
  appName: n8n
  databaseName: n8n
  roleName: n8n
  compositionRef:
    name: appdb-central-postgres
```

### Deployment changes (relevant env section)

```yaml
env:
  - name: DB_POSTGRESDB_USER
    valueFrom:
      secretKeyRef:
        name: n8n-db-connection
        key: username
  - name: DB_POSTGRESDB_PASSWORD
    valueFrom:
      secretKeyRef:
        name: n8n-db-connection
        key: password
```

### ConfigMap changes

```yaml
# Changed from: DB_POSTGRESDB_HOST: "n8n-postgres-rw"
DB_POSTGRESDB_HOST: "central-postgres-rw.postgres.svc.cluster.local"
```

### ExternalSecret changes

- Removed `DB_POSTGRESDB_PASSWORD` from template (now from Crossplane secret)
- Removed `n8n-postgres-credentials` ExternalSecret entirely
- Kept only non-DB secrets (`N8N_ENCRYPTION_KEY`)

### kustomization.yaml changes

- Removed `postgres.yaml`
- Added `appdb.yaml`
- Removed old postgres ExternalSecret entry

---

## Common Pitfalls

1. **`appName` must equal namespace** — The Crossplane composition creates the connection secret
   in the namespace matching `appName`. If these don't match, the secret lands in the wrong
   namespace.

2. **Underscore names** — PostgreSQL role and database names with underscores (e.g.,
   `vinyl_manager`) are valid. Keep them matching the old cluster's names.

3. **`postgresql+asyncpg://` vs `postgresql://`** — The Crossplane `uri` key uses standard
   `postgresql://` scheme. Apps using async drivers (SQLAlchemy async, etc.) need
   `postgresql+asyncpg://`. Either use individual keys or adjust the scheme.

4. **ExternalSecret with mixed secrets** — Some ExternalSecrets contain both DB credentials and
   other app secrets (e.g., API keys). Only remove the DB-related entries; keep the rest.

5. **`envFrom` vs `env`** — When removing an `envFrom.secretRef` for old postgres credentials,
   make sure to add individual `env` entries for the specific env var names the app expects
   (they may differ from the Crossplane secret keys).

6. **ArgoCD pruning** — ArgoCD with `prune: true` will delete the old CNPG Cluster and its PVC
   once `postgres.yaml` is removed from kustomization. This is the desired behavior but means
   **data migration must happen before cleanup**.

7. **Extensions** — If the old CNPG cluster used custom parameters (e.g., `jit=off`,
   `max_connections=200`), these are cluster-level settings on the central instance, not
   per-database. Evaluate if they're still needed or if the central cluster's settings suffice.

8. **pgvector** — For apps needing the `vector` extension (open-webui), add
   `extensions: [{name: vector}]` to the AppDBClaim spec.

9. **Multi-service apps** — Apps like garden have multiple deployments sharing the same database.
   Only create one AppDBClaim. Update all deployments that consume DB credentials.
