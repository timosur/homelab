#!/usr/bin/env bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
  cat <<'EOF'
Usage: ./scripts/update-k3s-cluster.sh [options]

Options:
  --yes          Run non-interactively where possible
  --skip-backup  Skip the backup playbook
  --preflight    Only run preflight checks
  --help         Show this help text
EOF
}

confirm() {
  local prompt="$1"

  if [[ "$AUTO_YES" == "true" ]]; then
    return 0
  fi

  echo -e -n "${YELLOW}[CONFIRM]${NC} ${prompt} [y/N] "
  read -r answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

check_dep() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "Required command not found: ${cmd}"
    exit 1
  fi
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
ANSIBLE_DIR="${REPO_ROOT}/ansible"
INVENTORY_FILE="${ANSIBLE_DIR}/inventory.yml"
UPDATE_PLAYBOOK="${ANSIBLE_DIR}/playbooks/k3s-update.yml"
BACKUP_PLAYBOOK="${ANSIBLE_DIR}/playbooks/cluster-backup.yml"

AUTO_YES="false"
SKIP_BACKUP="false"
PREFLIGHT_ONLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      AUTO_YES="true"
      ;;
    --skip-backup)
      SKIP_BACKUP="true"
      ;;
    --preflight)
      PREFLIGHT_ONLY="true"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      log_error "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
  shift
done

check_dep ansible-playbook
check_dep kubectl
check_dep awk

if [[ ! -f "$INVENTORY_FILE" ]]; then
  log_error "Inventory file not found: ${INVENTORY_FILE}"
  exit 1
fi

TARGET_VERSION=$(awk '/k3s_version:/ {print $2; exit}' "$INVENTORY_FILE")

if [[ -z "$TARGET_VERSION" ]]; then
  log_error "Could not determine k3s_version from ${INVENTORY_FILE}"
  exit 1
fi

print_intro() {
  echo ""
  echo "k3s cluster update"
  echo ""
  echo "  Repo root:       ${REPO_ROOT}"
  echo "  Inventory:       ${INVENTORY_FILE}"
  echo "  Target version:  ${TARGET_VERSION}"
  echo ""
  echo "This wrapper will:"
  echo "  1. Run an Ansible syntax check for the update playbook"
  echo "  2. Show current node status"
  echo "  3. Optionally run the backup playbook"
  echo "  4. Run the rolling k3s update playbook"
  echo ""
  echo "Expectations:"
  echo "  - Control plane updates first, then workers one at a time"
  echo "  - Worker drains now fail closed and abort the rollout"
  echo "  - Single-replica workloads may still see downtime during drain"
  echo ""
}

run_preflight() {
  log_info "Running syntax check for the update playbook"
  (
    cd "$ANSIBLE_DIR"
    ansible-playbook -i inventory.yml playbooks/k3s-update.yml --syntax-check
  )

  log_info "Current cluster nodes"
  kubectl get nodes -o wide
}

run_backup() {
  log_info "Running backup playbook before the update"
  (
    cd "$ANSIBLE_DIR"
    ansible-playbook -i inventory.yml playbooks/cluster-backup.yml
  )
}

run_update() {
  log_info "Starting rolling k3s update to ${TARGET_VERSION}"
  (
    cd "$ANSIBLE_DIR"
    ansible-playbook -i inventory.yml playbooks/k3s-update.yml
  )
}

print_follow_up() {
  echo ""
  log_success "Update playbook finished"
  echo ""
  echo "Recommended follow-up checks:"
  echo "  kubectl get nodes -o wide"
  echo "  kubectl get pods -A"
  echo "  kubectl get applications -n argocd"
  echo "  kubectl exec -n kube-system ds/cilium -- cilium status"
  echo ""
}

print_intro
run_preflight

if [[ "$PREFLIGHT_ONLY" == "true" ]]; then
  log_success "Preflight checks completed"
  exit 0
fi

if [[ "$SKIP_BACKUP" != "true" ]]; then
  if confirm "Run the backup playbook before updating?"; then
    run_backup
  else
    log_warning "Skipping backup at your request"
  fi
else
  log_warning "Skipping backup because --skip-backup was provided"
fi

if ! confirm "Continue with the rolling k3s update to ${TARGET_VERSION}?"; then
  log_warning "Aborted before running the update playbook"
  exit 0
fi

run_update
print_follow_up