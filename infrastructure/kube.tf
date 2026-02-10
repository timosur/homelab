###############################################
# kube.tf — Kube‑Hetzner v2.18.0
# Goal: low‑cost GitOps cluster w/ TLS on *.timosur.com
# Stack: k3s + Cilium + Envoy Gateway + Coraza WAF + Klipper LB + cert-manager
# Shape: 1× control plane (cpx11) + 2× workers (cpx11)
# LB: Klipper LB (built-in k3s load balancer)
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

variable "ssh_private_key" {
  type      = string
  sensitive = true
  default   = ""
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
  version = "2.19.1"

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

  # Allow scheduling on control plane, currently fsn1 region is disabled for new servers
  allow_scheduling_on_control_plane = true

  # —— Node pools ——
  control_plane_nodepools = [
    {
      name        = "cp"
      server_type = "cax21"
      location    = "fsn1"
      count       = 1
      labels      = ["workload-type=arm"]
      taints      = []
    }
  ]

  agent_nodepools = []

  # ——— Networking / LB ———
  cni_plugin         = "cilium"
  disable_kube_proxy = true

  enable_klipper_metal_lb = false

  cilium_version = "1.18.1"
  cilium_values  = <<EOT
cluster:
  name: "hetzner"
  id: 1
  
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

# Operator tolerations to ensure it can schedule during cluster initialization
operator:
  tolerations:
    - key: node.cloudprovider.kubernetes.io/uninitialized
      operator: Exists
      effect: NoSchedule
EOT

  load_balancer_type     = "lb11"
  load_balancer_location = "fsn1"

  # Disable ingress controller (using Envoy Gateway)
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
      description     = "Allow Apps to send email (SMTP)"
      direction       = "out"
      protocol        = "tcp"
      port            = "587"
      source_ips      = []
      destination_ips = ["0.0.0.0/0", "::/0"]
    },
    {
      description     = "Allow SMB (CIFS) to Storage Box"
      direction       = "out"
      protocol        = "tcp"
      port            = "445"
      source_ips      = []
      destination_ips = ["0.0.0.0/0", "::/0"]
    },
    {
      description     = "Allow VPN (WireGuard) to Home VPN"
      direction       = "out"
      protocol        = "tcp"
      port            = "57277"
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

output "control_planes_public_ipv4" {
  value = module.kube-hetzner.control_planes_public_ipv4
}

output "agents_public_ipv4" {
  value = module.kube-hetzner.agents_public_ipv4
}

# Configure static routes on control plane nodes
resource "null_resource" "configure_routes_control_plane" {
  depends_on = [module.kube-hetzner]
  count      = length(module.kube-hetzner.control_planes_public_ipv4)

  triggers = {
    node_ip = module.kube-hetzner.control_planes_public_ipv4[count.index]
  }

  provisioner "remote-exec" {
    inline = [
      "IFACE=$(ip route | grep 'default via 10.0.0.1' | awk '{print $5}' | head -n1)",
      "ip route add 192.168.255.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.1.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.2.0/24 via 10.0.0.1 dev $IFACE || true",
      "mkdir -p /etc/systemd/system",
      "cat > /usr/local/bin/static-route.sh << 'EOF'",
      "#!/bin/bash",
      "IFACE=$(ip route | grep 'default via 10.0.0.1' | awk '{print $5}' | head -n1)",
      "ip route add 192.168.255.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.1.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.2.0/24 via 10.0.0.1 dev $IFACE || true",
      "EOF",
      "chmod +x /usr/local/bin/static-route.sh",
      "cat > /etc/systemd/system/static-route.service << 'EOF'",
      "[Unit]",
      "Description=Add static route to home network",
      "After=network-online.target",
      "Wants=network-online.target",
      "[Service]",
      "Type=oneshot",
      "ExecStart=/usr/local/bin/static-route.sh",
      "RemainAfterExit=yes",
      "[Install]",
      "WantedBy=multi-user.target",
      "EOF",
      "systemctl daemon-reload",
      "systemctl enable static-route.service",
      "systemctl start static-route.service"
    ]

    connection {
      type        = "ssh"
      user        = "root"
      private_key = var.ssh_private_key
      host        = module.kube-hetzner.control_planes_public_ipv4[count.index]
    }
  }
}

# Configure static routes on agent nodes
resource "null_resource" "configure_routes_agents" {
  depends_on = [module.kube-hetzner]
  count      = length(module.kube-hetzner.agents_public_ipv4)

  triggers = {
    node_ip = module.kube-hetzner.agents_public_ipv4[count.index]
  }

  provisioner "remote-exec" {
    inline = [
      "IFACE=$(ip route | grep 'default via 10.0.0.1' | awk '{print $5}' | head -n1)",
      "ip route add 192.168.255.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.1.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.2.0/24 via 10.0.0.1 dev $IFACE || true",
      "mkdir -p /etc/systemd/system",
      "cat > /usr/local/bin/static-route.sh << 'EOF'",
      "#!/bin/bash",
      "IFACE=$(ip route | grep 'default via 10.0.0.1' | awk '{print $5}' | head -n1)",
      "ip route add 192.168.255.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.1.0/24 via 10.0.0.1 dev $IFACE || true",
      "ip route add 192.168.2.0/24 via 10.0.0.1 dev $IFACE || true",
      "EOF",
      "chmod +x /usr/local/bin/static-route.sh",
      "cat > /etc/systemd/system/static-route.service << 'EOF'",
      "[Unit]",
      "Description=Add static route to home network",
      "After=network-online.target",
      "Wants=network-online.target",
      "[Service]",
      "Type=oneshot",
      "ExecStart=/usr/local/bin/static-route.sh",
      "RemainAfterExit=yes",
      "[Install]",
      "WantedBy=multi-user.target",
      "EOF",
      "systemctl daemon-reload",
      "systemctl enable static-route.service",
      "systemctl start static-route.service"
    ]

    connection {
      type        = "ssh"
      user        = "root"
      private_key = var.ssh_private_key
      host        = module.kube-hetzner.agents_public_ipv4[count.index]
    }
  }
}
