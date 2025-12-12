# TODOs

- Find a way how to patch Argo Config with server insecure, to be able to expose it via gateway API -> Maybe deploy it via GitOps Pipeline
- Deploy Home Assistant to k3s home

## Pangolin

- How can I expose the gerbil TCP/UDP routes to the public on the same domain?
  -> Currently cilium gateway API does not support UDPRoutes and the Hetzner LB also does not support UDP
  -> Maybe I need to switch to MetalLB? How do I then get a public IP for my setup? Would I need to open a Port directly to my VM? This would mean gerbil could only run on one single node?

## Networking

- Switch the local k3s cluster to another CIDR, which is not conflicting with the hetzner k3s cluster
  --> (PodCIDR ranges in all clusters and all nodes must be non-conflicting and unique IP addresses.)

## ArgoCD

- How can I make it work that I can connect to the API server of my local k3s cluster? Can I use cilium cluster mesh for that? Can I somehow use natting for that within cilium?
  --> Should be possible after changing cluster cidr on local cluster

## Apps to be installed locally

1. Home Assistant
2. pihole
