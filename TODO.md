# TODOs

- Find a way how to patch Argo Config with server insecure, to be able to expose it via gateway API -> Maybe deploy it via GitOps Pipeline
- Deploy Home Assistant to k3s home

## Pangolin

- How can I expose the gerbil TCP/UDP routes to the public on the same domain?
  -> Currently cilium gateway API does not support UDPRoutes and the Hetzner LB also does not support UDP
  -> Maybe I need to switch to MetalLB? How do I then get a public IP for my setup? Would I need to open a Port directly to my VM? This would mean gerbil could only run on one single node?

## Cluster Mesh

- Add ip route to CP node
- Check how to exchange root ca certs in local cluster
- Open firewall on port 32379 or change to use the node internal IP, if possible for CP node on hetzner

## ArgoCD

- How can I make it work that I can connect to the API server of my local k3s cluster? Can I use cilium cluster mesh for that? Can I somehow use natting for that within cilium?

## Apps to be installed locally

1. Home Assistant
2. pihole
