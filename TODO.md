# TODOs

## Pangolin

- How can I expose the gerbil TCP/UDP routes to the public on the same domain?
  -> Currently cilium gateway API does not support UDPRoutes and the Hetzner LB also does not support UDP
  -> Maybe I need to switch to MetalLB? How do I then get a public IP for my setup? Would I need to open a Port directly to my VM? This would mean gerbil could only run on one single node?

## Apps to be installed locally

1. ArgoCD
2. Monitoring Stack (Prometheus, Node Exporter, Grafana, Loki)
3. Envoy Gateway
4. Home Assistant
5. pihole
6. Navidrome
