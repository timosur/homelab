###############################################
# kube.tf — Kube‑Hetzner v2.18.0
# Goal: low‑cost GitOps cluster w/ TLS on *.timosur.com
# Stack: k3s + Cilium + Cilium GatewayAPI + cert-manager
# Shape: 1× control plane (cpx11) + 2× workers (cpx11)
# LB: Hetzner Cloud Load Balancer (lb11) in fsn1 pointing to Cilium Gateway
###############################################

###############################################
# Locals & variables
###############################################
locals {
  project_name = "kh-gitops-timosur"
}

variable "hcloud_token" {
  sensitive = true
  type      = string
  default   = ""
}

variable "ssh_public_key_file" {
  type    = string
  default = ""
}

###############################################
# Terraform & provider
###############################################
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = ">= 1.51.0"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}

###############################################
# Module: kube-hetzner
###############################################
module "kube-hetzner" {
  source  = "kube-hetzner/kube-hetzner/hcloud"
  version = "2.18.1"

  providers    = { hcloud = hcloud }
  hcloud_token = var.hcloud_token

  # —— Region & network ——
  network_region = "eu-central"

  # —— Security ——
  firewall_ssh_source      = ["0.0.0.0/0", "::/0"]
  firewall_kube_api_source = ["0.0.0.0/0", "::/0"]

  # —— SSH keys ——
  ssh_public_key  = file(var.ssh_public_key_file)
  ssh_private_key = null

  # —— Node pools ——
  control_plane_nodepools = [
    {
      name        = "cp"
      server_type = "cax21"
      location    = "fsn1"
      count       = 1
      labels      = []
      taints      = []
    }
  ]

  agent_nodepools = [
    {
      name        = "workers"
      server_type = "cx32"
      location    = "fsn1"
      count       = 2
      labels      = []
      taints      = []
    },
    {
      name        = "workers-arm"
      server_type = "cax21"
      location    = "fsn1"
      count       = 1
      labels      = ["arch=arm64", "workload-type=arm"]
      taints      = []
    }
  ]

  # ——— Networking / LB ———
  cni_plugin         = "cilium"
  disable_kube_proxy = true

  # Enable Cilium Gateway API support
  cilium_version = "1.18.1"
  cilium_values  = <<EOT
# Enable Kubernetes host-scope IPAM mode (required for K3s + Hetzner CCM)
ipam:
  mode: kubernetes
k8s:
  requireIPv4PodCIDR: true

# Replace kube-proxy with Cilium
kubeProxyReplacement: true
# Enable health check server (healthz) for the kube-proxy replacement
kubeProxyReplacementHealthzBindAddr: "0.0.0.0:10256"

# Access to Kube API Server (mandatory if kube-proxy is disabled)
k8sServiceHost: "127.0.0.1"
k8sServicePort: "6444"

# Set Tunnel Mode or Native Routing Mode (supported by Hetzner CCM Route Controller)
routingMode: "tunnel"

# Perform a gradual roll out on config update.
rollOutCiliumPods: true

endpointRoutes:
  # Enable use of per endpoint routes instead of routing via the cilium_host interface.
  enabled: true

loadBalancer:
  # Enable LoadBalancer & NodePort XDP Acceleration (direct routing (routingMode=native) is recommended to achieve optimal performance)
  acceleration: native

bpf:
  # Enable eBPF-based Masquerading ("The eBPF-based implementation is the most efficient implementation")
  masquerade: true

encryption:
  enabled: true
  # Enable node encryption for node-to-node traffic
  nodeEncryption: true
  type: wireguard

debug:
  enabled: true

hubble:
  relay:
    enabled: true
  ui:
    enabled: true
  metrics:
    enabled:
      - dns
      - drop
      - tcp
      - flow
      - port-distribution
      - icmp
      - http

# Enable Gateway API support
gatewayAPI:
  enabled: true
  gatewayClass:
    create: "true"

# Operator tolerations to ensure it can schedule during cluster initialization
operator:
  tolerations:
    - key: node.cloudprovider.kubernetes.io/uninitialized
      operator: Exists
      effect: NoSchedule

MTU: 1450
EOT

  # Default LB that CCM will use when Services of type LoadBalancer are created
  load_balancer_type     = "lb11"
  load_balancer_location = "fsn1"

  ingress_controller = "none"

  # ——— TLS ———
  enable_cert_manager = true
  cert_manager_values = <<EOT
crds:
  enabled: true
  keep: true
replicaCount: 3
webhook:
  replicaCount: 3
cainjector:
  replicaCount: 3
config:
  enableGatewayAPI: true
EOT

  # Needs to be set until https://github.com/kube-hetzner/terraform-hcloud-kube-hetzner/issues/1887 is fixed
  kured_version = "1.19.0"

  extra_firewall_rules = [
    {
      description = "Allow Apps to send email (SMTP)"
      direction       = "out"
      protocol        = "tcp"
      port            = "587"
      source_ips      = []
      destination_ips = ["0.0.0.0/0", "::/0"]
    },
    {
      description = "Allow SMB (CIFS) to Storage Box"
      direction       = "out"
      protocol        = "tcp"
      port            = "445"
      source_ips      = []
      destination_ips = ["0.0.0.0/0", "::/0"]
    }
  ]
}


output "kubeconfig" {
  value     = module.kube-hetzner.kubeconfig
  sensitive = true
}


output "ingress_public_ipv4" {
  description = "Public IPv4 of the default ingress/load balancer (or first CP if none)."
  value       = module.kube-hetzner.ingress_public_ipv4
}
