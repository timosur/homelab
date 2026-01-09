# Homelab App Onboarding Guide

This guide documents the complete process for adding a new application to the homelab setup. Follow this checklist to ensure all components are properly configured.

## Prerequisites

- Application Docker image available
- Understanding of the app's requirements (database, volumes, environment variables)
- Domain name chosen (`<app>.timosur.com`)

## 1. Create App Directory Structure

Create the main app directory:

```
apps/<app-name>/
```

## 2. Core Kubernetes Manifests

### 2.1 Namespace (`namespace.yaml`)

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: <app-name>
```

### 2.2 Database (if needed) (`postgres.yaml`)

Use CloudNative-PG for PostgreSQL databases:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: <app-name>-postgres
  namespace: <app-name>
spec:
  instances: 1
  postgresql:
    parameters:
      max_connections: "200"
      shared_buffers: "256MB"
      effective_cache_size: "1GB"
  bootstrap:
    initdb:
      database: <app-name>
      owner: <app-name>
      secret:
        name: <app-name>-postgres-credentials
  storage:
    size: 10Gi # Adjust as needed
    storageClass: hcloud-volumes
```

### 2.3 Persistent Volume Claim (`pvc.yaml`)

For application data storage:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app-name>-data
  namespace: <app-name>
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 25Gi # Adjust size as needed
  storageClassName: hcloud-volumes
```

### 2.4 External Secrets (`external-secret.yaml`, `secret.yaml`)

**For PostgreSQL credentials** (`external-secret.yaml`):

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app-name>-postgres-credentials
  namespace: <app-name>
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: <app-name>-postgres-credentials
    creationPolicy: Owner
    template:
      type: kubernetes.io/basic-auth
  data:
    - secretKey: username
      remoteRef:
        key: <app-name>-postgres-username
    - secretKey: password
      remoteRef:
        key: <app-name>-postgres-password
```

**For app-specific secrets** (`secret.yaml`):

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app-name>-postgres-password
  namespace: <app-name>
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: azure-keyvault-store
    kind: ClusterSecretStore
  target:
    name: <app-name>-postgres-password
    creationPolicy: Owner
  data:
    - secretKey: POSTGRES_PASSWORD
      remoteRef:
        key: <app-name>-postgres-password
```

### 2.5 Configuration (`configmap.yaml`)

Application environment variables:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: <app-name>-config
  namespace: <app-name>
data:
  # App-specific configuration
  DATABASE_URL: "postgres://<app-name>:${POSTGRES_PASSWORD}@<app-name>-postgres-rw:5432/<app-name>"
  # Add other environment variables as needed
```

### 2.6 Deployment (`deployment.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: <app-name>
  namespace: <app-name>
spec:
  replicas: 1
  selector:
    matchLabels:
      app: <app-name>
  template:
    metadata:
      labels:
        app: <app-name>
    spec:
      # Use initContainers if directories need to be created
      initContainers:
        - name: init-directories
          image: busybox:1.36
          command: ["sh", "-c", "mkdir -p /app/data && chown -R 1000:1000 /app"]
          volumeMounts:
            - name: <app-name>-data
              mountPath: /app
      containers:
        - name: <app-name>
          image: <docker-image>:<tag>
          ports:
            - containerPort: <port>
          envFrom:
            - configMapRef:
                name: <app-name>-config
            - secretRef:
                name: <app-name>-postgres-password
          volumeMounts:
            - name: <app-name>-data
              mountPath: /app/data
              # Use subPath for multiple directories
              # subPath: subdirectory
          resources:
            limits:
              memory: "1000Mi"
              cpu: "1000m"
            requests:
              memory: "512Mi"
              cpu: "100m"
          livenessProbe:
            httpGet:
              path: /health # Adjust path
              port: <port>
            initialDelaySeconds: 30
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health # Adjust path
              port: <port>
            initialDelaySeconds: 5
            periodSeconds: 5
      volumes:
        - name: <app-name>-data
          persistentVolumeClaim:
            claimName: <app-name>-data
```

### 2.7 Service (`service.yaml`)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: <app-name>
  namespace: <app-name>
spec:
  selector:
    app: <app-name>
  ports:
    - protocol: TCP
      port: <port>
      targetPort: <port>
```

### 2.8 Kustomization (`kustomization.yaml`)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - namespace.yaml
  - postgres.yaml # If database needed
  - external-secret.yaml
  - configmap.yaml
  - secret.yaml
  - pvc.yaml
  - deployment.yaml
  - service.yaml

namespace: <app-name>
```

## 3. ArgoCD Integration

### 3.1 Environment-Specific Deployment

The homelab supports three deployment patterns:

1. **Shared Apps** (`apps/_argocd/`): Deploy to Hetzner environment only
2. **Home Apps** (`apps/_argocd-home/`): Deploy to home environment only  
3. **Shared Manifests** (`apps/<app-name>/`): Can be referenced by both environments

Choose the appropriate directory based on where the app should be deployed:

#### 3.1.1 Hetzner-Only Apps (`apps/_argocd/<app-name>-app.yaml`)

For apps that should only run in the Hetzner cloud environment:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <app-name>
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/timosur/homelab.git
    targetRevision: HEAD
    path: apps/<app-name>
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

#### 3.1.2 Home-Only Apps (`apps/_argocd-home/<app-name>-app.yaml`)

For apps that should only run in the home environment:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: <app-name>
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/timosur/homelab.git
    targetRevision: HEAD
    path: apps/<app-name>
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

#### 3.1.3 Environment-Specific Manifests

When apps need different configurations per environment, create environment-specific manifests:

- `apps/<app-name>-home/` - Home-specific manifests
- `apps/<app-name>-hetzner/` - Hetzner-specific manifests

Then reference the appropriate path in the ArgoCD application.

### 3.2 Update ArgoCD Kustomization

Add the new app to the appropriate kustomization file:

#### For Hetzner Apps (`apps/_argocd/kustomization.yaml`):

```yaml
resources:
  - networking-app.yaml
  - cert-manager-app.yaml
  - external-secrets-app.yaml
  - cloudnative-pg-app.yaml
  - mealie-app.yaml
  - open-webui-app.yaml
  - n8n-app.yaml
  - garden-app.yaml
  - <app-name>-app.yaml # Add this line
```

#### For Home Apps (`apps/_argocd-home/kustomization.yaml`):

```yaml
resources:
  - cloudnative-pg-app.yaml
  - external-secrets-app.yaml
  - garden-app.yaml
  - smb-csi-driver-app.yaml
  - <app-name>-app.yaml # Add this line
```

## 4. Networking Configuration

### 4.1 HTTP Route (`networking/httproutes/<app-name>-route.yaml`)

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: <app-name>
  namespace: <app-name>
spec:
  parentRefs:
    - name: envoy-gateway
      namespace: cert-manager
      sectionName: <app-name>-https
  hostnames:
    - "<app-name>.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: <app-name>
          port: <port>
```

### 4.2 Update HTTP Routes Kustomization (`networking/httproutes/kustomization.yaml`)

```yaml
resources:
  - argo-route.yaml
  - mealie-route.yaml
  - ai-route.yaml
  - n8n-route.yaml
  - garden-route.yaml
  - <app-name>-route.yaml # Add this line
```

### 4.3 Update Gateway (`networking/gateways/envoy-gateway.yaml`)

Add HTTP and HTTPS listeners:

```yaml
# Add these listeners to the existing gateway spec
- name: <app-name>-acme-http
  port: 80
  protocol: HTTP
  hostname: "<app-name>.timosur.com"
  allowedRoutes:
    namespaces:
      from: All
- name: <app-name>-https
  port: 443
  protocol: HTTPS
  hostname: "<app-name>.timosur.com"
  allowedRoutes:
    namespaces:
      from: All
  tls:
    mode: Terminate
    certificateRefs:
      - name: <app-name>-timosur-com
```

## 5. SSL Certificate Configuration

### 5.1 Update Cluster Issuer (`apps/cert-manager/cluster-issuer.yaml`)

Add a new solver for the domain:

```yaml
# Add this solver to the existing solvers list
- selector:
    dnsNames: ["<app-name>.timosur.com"]
  http01:
    gatewayHTTPRoute:
      parentRefs:
        - group: gateway.networking.k8s.io
          kind: Gateway
          name: envoy-gateway
          namespace: cert-manager
          sectionName: <app-name>-acme-http
```

## 6. Azure Key Vault Secrets

Add the following secrets to Azure Key Vault:

- `<app-name>-postgres-username` (value: `<app-name>`)
- `<app-name>-postgres-password` (generate secure password)
- Any other app-specific secrets

## 7. DNS Configuration

Point `<app-name>.timosur.com` to your cluster's external IP address.

## 8. Deployment Checklist

### Pre-deployment:

- [ ] All manifests created in `apps/<app-name>/`
- [ ] ArgoCD application created in appropriate directory:
  - [ ] `apps/_argocd/` for Hetzner-only apps
  - [ ] `apps/_argocd-home/` for home-only apps
- [ ] ArgoCD application added to correct kustomization file
- [ ] HTTP route created and added to kustomization
- [ ] Gateway listeners added
- [ ] Cluster issuer solver added
- [ ] Secrets added to Azure Key Vault
- [ ] DNS configured

### Post-deployment:

- [ ] ArgoCD shows application as synced and healthy
- [ ] Pods are running and ready
- [ ] Service is accessible
- [ ] SSL certificate is issued
- [ ] Database connection working (if applicable)
- [ ] Application functionality verified

## Common Patterns

### Volume Mounts with Subdirectories

When an app needs multiple directories on the same volume:

```yaml
volumeMounts:
  - name: app-data
    mountPath: /app/uploads
    subPath: uploads
  - name: app-data
    mountPath: /app/config
    subPath: config
```

### Init Containers for Directory Setup

```yaml
initContainers:
  - name: init-directories
    image: busybox:1.36
    command: ["sh", "-c", "mkdir -p /app/uploads /app/config && chown -R 1000:1000 /app"]
    volumeMounts:
      - name: app-data
        mountPath: /app
```

### Environment Variable Pattern

- Use ConfigMaps for non-sensitive configuration
- Use External Secrets for sensitive data
- Reference secrets as environment variables in the deployment

### Database Connection Pattern

- Use CloudNative-PG for PostgreSQL
- Store credentials in Azure Key Vault
- Use External Secrets to sync credentials
- Reference connection details in ConfigMap with environment variable substitution

## Storage Guidelines

- **Database storage**: Start with 10Gi, adjust based on needs
- **Application data**: Start with 25Gi, adjust based on needs
- **Always use**: `storageClassName: hcloud-volumes`
- **Access mode**: `ReadWriteOnce` for single-pod applications

## Security Best Practices

- Never store passwords in plain text
- Use External Secrets for all sensitive data
- Set appropriate resource limits
- Use health checks (liveness and readiness probes)
- Run containers as non-root when possible
- Use specific image tags, avoid `:latest`

This guide provides a complete template for onboarding new applications to the homelab infrastructure while following established patterns and best practices.
