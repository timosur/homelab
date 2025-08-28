###############################################
# kube.tf — Kube‑Hetzner v2.18.0
# Goal: low‑cost GitOps cluster w/ wildcard TLS on *.timosur.com
# Stack: k3s + Cilium + NGINX Ingress + cert-manager (DNS‑01 for wildcard)
# Shape: 1× control plane (cpx11) + 2× workers (cpx11)
# LB: Hetzner Cloud Load Balancer (lb11) in fsn1
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
  type      = string
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
  version = "2.18.1"

  providers   = { hcloud = hcloud }
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
      server_type = "cpx11"
      location    = "fsn1"
      count       = 1
      labels      = []
      taints      = []
    }
  ]

  agent_nodepools = [
    {
      name        = "workers"
      server_type = "cpx11"
      location    = "fsn1"
      count       = 2
      labels      = []
      taints      = []
    }
  ]

  # ——— Networking / LB ———
  cni_plugin = "cilium"
  disable_kube_proxy = true


  # Default LB that CCM will use when Services of type LoadBalancer are created
  load_balancer_type = "lb11"
  load_balancer_location = "fsn1"


  # Don’t deploy Traefik/Nginx/HAProxy; we’ll use Cilium Gateway API instead
  ingress_controller = "none"


  # Minimal Cilium Helm values: enable Gateway API dataplane & L7 Proxy
  cilium_values = <<-EOT
kubeProxyReplacement: true
l7Proxy: true
k8s:
  requireIPv4PodCIDR: true
gatewayAPI:
  enabled: true
  EOT


  # ——— TLS ———
  enable_cert_manager = true
  # Tip: create a Hetzner DNS API secret + ClusterIssuer/Certificate via Kustomize or HelmChart manifests
  # for *.timosur.com, then define a Gateway & HTTPRoutes using the issued secret.


  # ——— Convenience ———
  # create_kubeconfig = false # (recommended for CI; fetch later via: terraform output --raw kubeconfig > kubeconfig.yaml)
  # export_values = true # (optional) export effective Helm values for charts
}


output "kubeconfig" {
  value = module.kube-hetzner.kubeconfig
  sensitive = true
}


output "ingress_public_ipv4" {
  description = "Public IPv4 of the default ingress/load balancer (or first CP if none)."
  value = module.kube-hetzner.ingress_public_ipv4
} 