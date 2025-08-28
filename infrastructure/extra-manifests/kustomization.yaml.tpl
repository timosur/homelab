apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

# Multi-namespace deployment using overlays
resources:
- overlays/argocd

# Global configurations
commonLabels:
  homelab.io/deployment: terraform-managed

# Optional: Add global patches here
patchesStrategicMerge: []