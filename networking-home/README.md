# Home Cluster Networking

This folder contains the networking configuration for your home cluster, designed for HTTP-only access with the domain pattern `*.home.timosur.com`.

## Features

- **HTTP-only**: No HTTPS/TLS complexity for home use
- **Central Gateway**: Single envoy-gateway for all services
- **K3s ServiceLB**: Uses built-in load balancer
- **Control Plane Only**: Routes traffic only through control plane nodes
- **External DNS**: Automatic DNS record management for `*.home.timosur.com`

## Architecture

```
Internet → Router → Control Plane Node → K3s ServiceLB → Envoy Gateway → Services
```

## Setup Instructions

### 1. Configure Control Plane IP

Before deploying, you need to update the gateway configuration with your actual control plane node IP:

1. Find your control plane node IP:
   ```bash
   kubectl get nodes -o wide
   ```

2. Edit `gateways/envoy-gateway-home.yaml` and replace `control-plane-ip` with your actual IP:
   ```yaml
   annotations:
     metallb.universe.tf/loadBalancerIPs: "YOUR_CONTROL_PLANE_IP"
   ```

### 2. External Secrets Setup

Ensure you have an Azure SecretStore configured in the `external-secrets` namespace with the following secrets:
- `dns-client-id`
- `dns-client-secret`
- `dns-tenant-id`
- `dns-subscription-id`
- `dns-resource-group`

### 3. Deploy via ArgoCD

The networking is automatically deployed via the `networking-home-app.yaml` in the `_argocd-home` folder.

## Adding New Services

To add a new service to the home cluster networking:

1. Create an HTTPRoute in the `httproutes/` folder:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: myservice-home
  namespace: myservice-namespace
spec:
  parentRefs:
  - name: envoy-gateway-home
    namespace: envoy-gateway-system
  hostnames:
  - "myservice.home.timosur.com"
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /
    backendRefs:
    - name: myservice-service
      port: 8080
```

2. Add the new file to `httproutes/kustomization.yaml`:

```yaml
resources:
- myservice-httproute.yaml
```

## DNS Configuration

External-DNS will automatically create DNS records for all HTTPRoutes with hostnames matching `*.home.timosur.com`. The records will point to your control plane node IP.

## Troubleshooting

### Check Gateway Status
```bash
kubectl get gateway envoy-gateway-home -n envoy-gateway-system
```

### Check External-DNS Logs
```bash
kubectl logs -n external-dns-home deployment/external-dns-home
```

### Check HTTPRoute Status
```bash
kubectl get httproute -A
```

### Verify LoadBalancer Service
```bash
kubectl get svc -n envoy-gateway-system
```

## Network Policies

This configuration doesn't include Cilium network policies. Add them to the main `networking` folder if needed for the home cluster.