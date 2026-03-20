#!/usr/bin/env bash
set -euo pipefail

APP="bike-weather-preview"
NAMESPACE="bike-weather-preview"
DEPLOYMENTS=(
  "bike-weather-preview-agent"
  "bike-weather-preview-backend"
  "bike-weather-preview-frontend"
  "bike-weather-preview-nginx"
)

usage() {
  echo "Usage: $0 {up|down|status}"
  echo
  echo "  up     — Suspend ArgoCD auto-sync and scale preview deployments to 1"
  echo "  down   — Scale preview deployments to 0 and re-enable ArgoCD auto-sync"
  echo "  status — Show current replica counts and ArgoCD sync status"
  exit 1
}

check_deps() {
  if ! command -v kubectl &>/dev/null; then
    echo "Error: 'kubectl' not found in PATH" >&2
    exit 1
  fi
}

get_sync_status() {
  kubectl get application "$APP" -n argocd -o jsonpath='{.spec.syncPolicy.automated}' 2>/dev/null
}

suspend_autosync() {
  echo "Suspending ArgoCD auto-sync for $APP..."
  kubectl patch application "$APP" -n argocd --type=json \
    -p='[{"op": "remove", "path": "/spec/syncPolicy/automated"}]' 2>/dev/null || true
}

resume_autosync() {
  echo "Re-enabling ArgoCD auto-sync for $APP..."
  kubectl patch application "$APP" -n argocd --type=merge \
    -p='{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
}

scale_up() {
  echo "Scaling up preview deployments..."
  for deploy in "${DEPLOYMENTS[@]}"; do
    kubectl scale deployment "$deploy" -n "$NAMESPACE" --replicas=1
  done
  echo
  echo "Waiting for rollout..."
  for deploy in "${DEPLOYMENTS[@]}"; do
    kubectl rollout status deployment "$deploy" -n "$NAMESPACE" --timeout=120s || true
  done
  echo
  echo "Preview environment is UP."
}

scale_down() {
  echo "Scaling down preview deployments..."
  for deploy in "${DEPLOYMENTS[@]}"; do
    kubectl scale deployment "$deploy" -n "$NAMESPACE" --replicas=0
  done
  echo "Preview deployments scaled to 0."
}

show_status() {
  echo "=== Deployment replicas ==="
  for deploy in "${DEPLOYMENTS[@]}"; do
    replicas=$(kubectl get deployment "$deploy" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "N/A")
    ready=$(kubectl get deployment "$deploy" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    printf "  %-40s replicas=%s  ready=%s\n" "$deploy" "$replicas" "${ready:-0}"
  done
  echo
  echo "=== Postgres cluster ==="
  kubectl get cluster "$APP-postgres" -n "$NAMESPACE" -o jsonpath='  instances={.spec.instances}  ready={.status.readyInstances}' 2>/dev/null || echo "  N/A"
  echo
  echo
  auto_sync=$(get_sync_status)
  if [[ -n "$auto_sync" ]]; then
    echo "=== ArgoCD auto-sync: ENABLED ==="
  else
    echo "=== ArgoCD auto-sync: SUSPENDED (preview may be running) ==="
  fi
}

check_deps

case "${1:-}" in
  up)
    suspend_autosync
    scale_up
    ;;
  down)
    scale_down
    resume_autosync
    echo
    echo "ArgoCD will self-heal to match Git (replicas: 0). Preview is DOWN."
    ;;
  status)
    show_status
    ;;
  *)
    usage
    ;;
esac
