# Networking

This folder contains the networking configuration for the homelab cluster.

## Features

- **Two Gateways**: `envoy-gateway-home` (LAN, `*.home.timosur.com`) and `envoy-gateway-internet` (public, `*.timosur.com`)
- **Cilium LB IPAM**: L2 announcements for load balancer IPs
- **TLS**: cert-manager with Let's Encrypt DNS-01 via Cloudflare (internet gateway)
- **DNS**: Managed via Unifi router, updating `*.timosur.com` wildcard in Cloudflare
- **Network Policies**: Cilium-based network isolation

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

### 2. Deploy via ArgoCD

The networking is automatically deployed via the `networking-app.yaml` in the `_argocd` folder.

## Adding New Services

To add a new service:

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

DNS is managed externally via the Unifi router, which updates the `*.timosur.com` wildcard record in Cloudflare to point to the correct public IP.

## Troubleshooting

### Check Gateway Status
```bash
kubectl get gateway envoy-gateway-home -n envoy-gateway-system
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