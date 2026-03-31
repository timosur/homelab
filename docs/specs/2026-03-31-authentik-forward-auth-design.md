# Authentik Forward-Auth for WOL-Proxy Internet Services

## Problem

The 4 internet-facing services (mealie, actual, n8n, paperless) route through the wol-proxy, which sends a Wake-on-LAN packet to the desktop node on every request. Bots and crawlers trigger unwanted wakes.

## Solution

A new, independent Authentik instance at `auth.timosur.com` protects the WOL-proxy internet routes via Envoy Gateway SecurityPolicy (ExtAuth). Only authenticated users can wake the desktop. Home network routes (`*.home.timosur.com`) remain unprotected.

## Architecture

```
Internet Request (e.g. docs.timosur.com)
    |
Envoy Gateway Internet (192.168.2.254)
    |
SecurityPolicy (extAuth) --> Authentik Embedded Outpost (auth.timosur.com)
    |
    +-- Not authenticated --> Redirect to auth.timosur.com login
    +-- Authenticated --> HTTPRoute --> wol-proxy --> Desktop wakes up
```

### SSO Behavior

All 4 protected services share a single Authentik session. One login grants access to all services (docs, finance, automate, mealie). No re-authentication needed when switching between services.

## Components

### 1. Authentik Instance (`apps/authentik/`)

A new, independent Authentik deployment (separate from the existing `bike-weather-auth` instance).

**Resources:**
- **Server Deployment** - Authentik server on port 9000 (HTTPS 9443), handles UI and API
- **Worker Deployment** - Background task worker (email, sync, etc.)
- **Redis Deployment** - Session and cache storage
- **PostgreSQL** - Via AppDBClaim (Crossplane), central-postgres cluster
- **Secrets** - Via ExternalSecret from Azure Key Vault:
  - `authentik-secret-key` - Authentik encryption key
  - SMTP credentials (if email needed)
- **Media PVC** - Persistent storage for media/uploads
- **ConfigMap** - Environment configuration (DB host, Redis host, email settings)

**Deployment constraints:**
- Server and Worker run on `homelab-amd` (control plane, always on) to avoid chicken-and-egg with WOL
- Redis runs alongside in the same namespace

### 2. Networking: Gateway & HTTPRoute

**Internet Gateway** (`networking/gateways/internet/gateway.yaml`):

- No changes needed. The existing wildcard listener `https` on `*.timosur.com` already covers `auth.timosur.com`.

**New HTTPRoute** (`networking/httproutes/internet/authentik.yaml`):
- Hostname: `auth.timosur.com`
- Parent: `envoy-gateway-internet` listener `https` (wildcard)
- Backend: `authentik-server:9000` in `authentik` namespace
- No ReferenceGrant needed — gateway has `allowedRoutes.namespaces.from: All`

### 3. SecurityPolicy for WOL-Proxy Routes (`networking/security-policies/`)

A single Envoy Gateway `SecurityPolicy` resource that applies ExtAuth to the 4 WOL-proxy internet HTTPRoutes.

**Implementation approach:** One SecurityPolicy per protected HTTPRoute (4 total), each in the same namespace as the HTTPRoute, targeting the specific route. This is required because SecurityPolicy must target resources in the same namespace.

**SecurityPolicy spec:**
```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: wol-auth-<service>
  namespace: <service-namespace>
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: <route-name>
  extAuth:
    http:
      backendRef:
        name: authentik-server
        namespace: authentik
        port: 9000
      path: /outpost.goauthentik.io/auth/envoy
      headersToBackend:
        - cookie
        - authorization
```

**Protected routes:**
| Service   | Namespace | HTTPRoute name | Hostname             |
| --------- | --------- | -------------- | -------------------- |
| Paperless | paperless | paperless      | docs.timosur.com     |
| Actual    | actual    | actual         | finance.timosur.com  |
| N8N       | n8n       | n8n            | automate.timosur.com |
| Mealie    | mealie    | mealie         | mealie.timosur.com   |

**Note:** The `auth.timosur.com` HTTPRoute itself must NOT have a SecurityPolicy (would create a redirect loop).

### 4. Network Policies

- Allow ingress from `envoy-gateway-internet-system` to `authentik` namespace on port 9000 (serves both the HTTPRoute traffic and ExtAuth subrequests, since Envoy sends both)

### 5. Unmodified Components

- **Home gateway** (`*.home.timosur.com`) - No changes, no auth
- **WOL-proxy** - No code changes needed
- **bike-weather-auth** - Remains independent, unchanged
- **Existing internet HTTPRoutes** - Route definitions stay the same, only SecurityPolicy is added on top

## File Structure

```
apps/authentik/                         # NEW - Authentik deployment
  namespace.yaml
  kustomization.yaml
  server-deployment.yaml
  worker-deployment.yaml
  server-service.yaml
  redis-deployment.yaml
  redis-service.yaml
  configmap.yaml
  external-secret.yaml
  appdb.yaml
  media-pvc.yaml

apps/_argocd/
  authentik-app.yaml                    # NEW - ArgoCD Application
  kustomization.yaml                    # MODIFY - add authentik-app.yaml

networking/httproutes/internet/
  authentik.yaml                        # NEW - HTTPRoute for auth.timosur.com

networking/security-policies/           # NEW directory
  kustomization.yaml
  paperless-auth.yaml                   # SecurityPolicy for paperless
  actual-auth.yaml                      # SecurityPolicy for actual
  n8n-auth.yaml                         # SecurityPolicy for n8n
  mealie-auth.yaml                      # SecurityPolicy for mealie
```

## Post-Deployment: Authentik UI Configuration

After the Kubernetes resources are deployed, manual configuration in the Authentik admin UI (`auth.timosur.com/if/admin/`) is required:

1. **Create Proxy Provider** (Forward Auth mode, single provider for all domains)
   - Name: `wol-proxy-forward-auth`
   - Authorization flow: default
   - External host: `https://auth.timosur.com`
   - Mode: Forward auth (single application)

2. **Create Application**
   - Name: `WOL Proxy Services`
   - Provider: `wol-proxy-forward-auth`
   - Launch URL: blank

3. **Configure Embedded Outpost**
   - Add the `WOL Proxy Services` application to the embedded outpost
   - The outpost will handle `/outpost.goauthentik.io/*` paths automatically

4. **Create Users** (if not using external identity providers)

## Risks & Considerations

- **Chicken-and-egg:** Authentik must run on the always-on control plane node, not on the desktop node that gets woken by WOL
- **Authentik updates:** New Authentik version may change outpost API paths; pin to a specific version
- **Session duration:** Configure appropriate session lifetime (e.g., 14 days with remember-me) to avoid frequent re-logins
- **Mobile apps:** If any of the 4 services have mobile apps that use API tokens, those requests need to pass through auth too - may need API token allowlisting in Authentik
- **SecurityPolicy and cross-namespace backendRef:** Envoy Gateway SecurityPolicy ExtAuth with cross-namespace backend references may require a ReferenceGrant from the authentik namespace. This needs to be verified during implementation.
