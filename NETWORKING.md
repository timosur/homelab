# Networking

## Overview

The cluster runs two separate Envoy Gateway instances — one for **home/LAN** traffic and one for **internet-facing** traffic. DNS is managed via DynDNS on the Unifi Dream Router (UDR), which keeps the Cloudflare A records for `*.timosur.com`, `timosur.com`, and `givgroov.de` pointed at the router's current public IP. TLS certificates for internet services are provisioned automatically via cert-manager with Let's Encrypt DNS-01 challenges through Cloudflare.

## Architecture

```
Internet → UDR (port forward) → 192.168.2.254 → envoy-gateway-internet → Internet Services
LAN Client → 192.168.2.100 → envoy-gateway-home → Home Services
```

### Traffic Flow

1. **Internet traffic**: The UDR forwards ports 80/443 to `192.168.2.254`, the Cilium-assigned IP for the internet gateway. Envoy terminates TLS and routes to backend services.
2. **Home/LAN traffic**: LAN clients access `*.home.timosur.com` which resolves (via local DNS or the wildcard) to `192.168.2.100`, the Cilium-assigned IP for the home gateway. Traffic is plain HTTP — no TLS.

### DNS

DNS is **not** managed by the cluster. The UDR runs a DynDNS client that updates Cloudflare A records for `*.timosur.com`, `timosur.com`, and `givgroov.de` whenever the public IP changes. All subdomains (`mealie.timosur.com`, `ai.timosur.com`, etc.) resolve via the `*.timosur.com` wildcard. Home services use `*.home.timosur.com` which is resolved via local DNS (e.g. Pi-hole).

## Folder Structure

```
networking/
├── kustomization.yaml                    # Root — includes all subdirectories
├── cilium-lb-ipam/                       # IP address management for LoadBalancers
│   ├── ip-pools.yaml                     #   Dedicated IPs per gateway (192.168.2.100, 192.168.2.254)
│   ├── l2-announcement.yaml             #   ARP announcements so the router can find these IPs
│   └── kustomization.yaml
├── gateways/
│   ├── home/                             # Home/LAN gateway (*.home.timosur.com)
│   │   ├── gateway.yaml                 #   GatewayClass + Gateway on port 80
│   │   └── kustomization.yaml
│   └── internet/                         # Internet gateway (*.timosur.com, timosur.com, givgroov.de)
│       ├── namespace.yaml               #   envoy-gateway-internet-system namespace
│       ├── gateway.yaml                 #   GatewayClass + Gateway on ports 80/443 with TLS
│       ├── wildcard-certificate.yaml    #   *.timosur.com cert via Let's Encrypt
│       ├── givgroov-certificate.yaml    #   givgroov.de cert via Let's Encrypt
│       ├── http-to-https-redirect.yaml  #   Redirects all HTTP → HTTPS
│       └── kustomization.yaml
├── httproutes/
│   ├── home/                             # Routes for LAN-only services
│   │   ├── actual.yaml                  #   finance.home.timosur.com
│   │   ├── argocd.yaml                  #   argo.home.timosur.com
│   │   ├── garden.yaml                  #   garden.home.timosur.com
│   │   ├── home-assistant.yaml          #   ha.home.timosur.com
│   │   ├── paperless.yaml               #   docs.home.timosur.com
│   │   ├── pi-hole.yaml                #   pihole.home.timosur.com
│   │   ├── vinyl-manager.yaml           #   vinyl.home.timosur.com
│   │   └── kustomization.yaml
│   └── internet/                         # Routes for public services
│       ├── givgroov.yaml                #   givgroov.de
│       ├── mealie.yaml                  #   mealie.timosur.com
│       ├── open-webui.yaml              #   ai.timosur.com
│       ├── portfolio.yaml               #   timosur.com
│       └── kustomization.yaml
└── cilium-network-policies/
    ├── default-deny-ingress.yaml         # Cluster-wide default deny ingress (excludes system namespaces)
    ├── kustomization.yaml
    ├── home/                             # Policies for the home gateway
    │   ├── gateway-lan-only.yaml        #   Only allow ingress from LAN + cluster CIDRs
    │   └── kustomization.yaml
    └── internet/                         # Policies for the internet gateway
        ├── gateway-isolation.yaml       #   Prevent gateway pods from reaching LAN
        ├── workload-egress-deny-lan.yaml #   Deny LAN access for namespaces labeled exposure=internet
        └── kustomization.yaml
```

## Components

### Cilium LB IPAM

Cilium assigns stable IPs to each gateway's LoadBalancer service via `CiliumLoadBalancerIPPool` resources. The `CiliumL2AnnouncementPolicy` responds to ARP requests so the router and LAN clients can reach these IPs.

| Gateway  | IP              | Purpose           |
|----------|-----------------|-------------------|
| Home     | 192.168.2.100   | LAN services      |
| Internet | 192.168.2.254   | Public services   |

### Gateways

Two separate `GatewayClass` + `Gateway` pairs using Envoy:

- **Home** (`envoy-gateway-home`): Single HTTP listener on port 80 for `*.home.timosur.com`. No TLS — LAN only.
- **Internet** (`envoy-gateway-internet`): Listeners on ports 80 and 443 for `*.timosur.com`, `timosur.com`, and `givgroov.de`. TLS terminated with wildcard (`*.timosur.com`) and individual (`givgroov.de`) certificates from cert-manager. HTTP automatically redirects to HTTPS.

### HTTPRoutes

Routes are split into `home/` and `internet/` subdirectories. Each YAML file defines an HTTPRoute pointing to a backend service.

### Network Policies (Cilium)

Four policies enforce network segmentation:

1. **`default-deny-ingress.yaml`** — Cluster-wide default deny ingress (`CiliumClusterwideNetworkPolicy`). All non-system namespaces have ingress denied by default. Each namespace must explicitly allow the ingress it needs. Allows kubelet health probes, intra-namespace traffic, and traffic from both envoy gateway namespaces. System namespaces (kube-system, cilium-system, cert-manager, argocd, etc.) are excluded.

2. **`home/gateway-lan-only.yaml`** — The home gateway only accepts ingress from LAN (`192.168.0.0/16`) and cluster-internal CIDRs. Prevents internet traffic from reaching home services even if port forwarding is misconfigured.

3. **`internet/gateway-isolation.yaml`** — Internet gateway pods cannot reach LAN subnets (`192.168.0.0/16`, `172.16.0.0/12`). Allows only DNS, pod CIDR, and service CIDR egress. Prevents lateral movement if a gateway pod is compromised.

4. **`internet/workload-egress-deny-lan.yaml`** — Cluster-wide policy. Any namespace labeled `exposure: internet` gets restricted egress — LAN subnets are blocked. Apply with:
   ```bash
   kubectl label ns <namespace> exposure=internet
   ```

## Adding a New Service

### Home (LAN-only)

1. Create `networking/httproutes/home/<service>.yaml`:
   ```yaml
   apiVersion: gateway.networking.k8s.io/v1
   kind: HTTPRoute
   metadata:
     name: myservice
     namespace: myservice
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
           - name: myservice
             port: 8080
   ```
2. Add to `networking/httproutes/home/kustomization.yaml`

### Internet (Public)

1. Create `networking/httproutes/internet/<service>.yaml`:
   ```yaml
   apiVersion: gateway.networking.k8s.io/v1
   kind: HTTPRoute
   metadata:
     name: myservice
     namespace: myservice
   spec:
     parentRefs:
       - name: envoy-gateway-internet
         namespace: envoy-gateway-internet-system
         sectionName: https
     hostnames:
       - "myservice.timosur.com"
     rules:
       - matches:
           - path:
               type: PathPrefix
               value: /
         backendRefs:
           - name: myservice
             port: 8080
   ```
2. Add to `networking/httproutes/internet/kustomization.yaml`
3. Label the namespace: `kubectl label ns myservice exposure=internet`

## Troubleshooting

```bash
# Check gateway status
kubectl get gateway -A

# Check HTTPRoute status
kubectl get httproute -A

# Check LoadBalancer IPs
kubectl get svc -A | grep LoadBalancer

# Check Cilium L2 announcements
kubectl get ciliuml2announcementpolicy

# Verify TLS certificates
kubectl get certificates -n envoy-gateway-internet-system
```
