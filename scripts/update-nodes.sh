#!/usr/bin/env bash
# update-nodes.sh — Update and reboot all homelab nodes in safe order.
#
# Order:
#   1. Wake WoL nodes (homelab-gpu, homelab-amd-desktop) via homelab-amd
#   2. Wait for WoL nodes to become reachable
#   3. Update packages on all worker nodes (one at a time: drain → apt upgrade → reboot → wait → uncordon)
#   4. Update and reboot the control plane (homelab-amd) last
#
# Requirements (on this machine): ssh access to homelab-amd with key auth

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────────────
CONTROL_PLANE="homelab-amd"
WORKERS=(homelab-arm-small homelab-arm-large)
WOL_NODES=(homelab-gpu homelab-amd-desktop)
WOL_MACS=(
  "homelab-gpu:2c:f0:5d:05:9d:80"
  "homelab-amd-desktop:30:9c:23:8a:30:e3"
)
ALL_WORKERS=("${WORKERS[@]}" "${WOL_NODES[@]}")
ALL_NODES=("${ALL_WORKERS[@]}" "$CONTROL_PLANE")

declare -A SSH_TARGETS=(
  [homelab-amd]="homelab-amd"
  [homelab-arm-small]="homelab-arm-small"
  [homelab-arm-large]="homelab-arm-large"
  [homelab-gpu]="192.168.2.47"
  [homelab-amd-desktop]="192.168.2.241"
)

KUBECONFIG_PATH="/etc/rancher/k3s/k3s.yaml"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
WOL_WAIT_SECONDS=120   # max wait for WoL node to become reachable
WOL_POLL_INTERVAL=10
REBOOT_WAIT_SECONDS=300 # max wait for node to come back after reboot
REBOOT_POLL_INTERVAL=15

declare -A PENDING_UPDATES
declare -A REBOOT_REQUIRED

# ─── Helpers ──────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
info() { log "INFO  $*"; }
ok()   { log "OK    $*"; }
warn() { log "WARN  $*"; }
die()  { log "ERROR $*" >&2; exit 1; }

filter_ssh_banner() {
  sed -E \
    -e '/^\*{10,}$/d' \
    -e '/Unauthorized access to this system is prohibited\./d' \
    -e '/All connections are monitored and recorded\./d' \
    -e '/Disconnect IMMEDIATELY if you are not an authorized user\./d'
}

run_on() {
  local host="$1"; shift
  local command="$*"
  local target="${SSH_TARGETS[$host]:-$host}"

  if [[ "$host" == "$CONTROL_PLANE" ]]; then
    ssh $SSH_OPTS "$CONTROL_PLANE" "$command" 2>&1 | filter_ssh_banner
    return
  fi

  ssh $SSH_OPTS "$CONTROL_PLANE" "ssh $SSH_OPTS $target $(printf '%q' "$command")" 2>&1 | filter_ssh_banner
}

get_node_update_state() {
  local host="$1"
  run_on "$host" "sudo apt-get update -qq >/dev/null; updates=\$(apt list --upgradable 2>/dev/null | awk 'NR > 1 && NF {count++} END {print count+0}'); if [[ -f /var/run/reboot-required ]]; then reboot_required=yes; else reboot_required=no; fi; printf '%s|%s\n' \"\$updates\" \"\$reboot_required\""
}

collect_update_plan() {
  local host state updates reboot_required

  info "=== Step 3: Checking pending updates and reboot requirements ==="
  printf '%-22s %-10s %-15s %s\n' "Node" "Updates" "RebootRequired" "Action"
  printf '%-22s %-10s %-15s %s\n' "----" "-------" "---------------" "------"

  for host in "${ALL_NODES[@]}"; do
    state=$(get_node_update_state "$host")
    updates="${state%%|*}"
    reboot_required="${state##*|}"

    PENDING_UPDATES[$host]="$updates"
    REBOOT_REQUIRED[$host]="$reboot_required"

    if [[ "$updates" -gt 0 && "$reboot_required" == "yes" ]]; then
      printf '%-22s %-10s %-15s %s\n' "$host" "$updates" "$reboot_required" "update+reboot"
    elif [[ "$updates" -gt 0 ]]; then
      printf '%-22s %-10s %-15s %s\n' "$host" "$updates" "$reboot_required" "update"
    elif [[ "$reboot_required" == "yes" ]]; then
      printf '%-22s %-10s %-15s %s\n' "$host" "$updates" "$reboot_required" "reboot"
    else
      printf '%-22s %-10s %-15s %s\n' "$host" "$updates" "$reboot_required" "skip"
    fi
  done
}

confirm_update_plan() {
  local answer
  printf '\n'
  read -r -p "Continue with this update plan? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES)
      ok "Continuing with update run"
      ;;
    *)
      die "Aborted by user"
      ;;
  esac
}

kubectl_on_cp() {
  ssh $SSH_OPTS "$CONTROL_PLANE" "sudo KUBECONFIG=$KUBECONFIG_PATH kubectl $*" 2>&1 | filter_ssh_banner
}

wait_for_ssh() {
  local host="$1"
  local target="${SSH_TARGETS[$host]:-$host}"
  local deadline=$(( $(date +%s) + WOL_WAIT_SECONDS ))
  info "Waiting for $host to become reachable (max ${WOL_WAIT_SECONDS}s)..."
  while (( $(date +%s) < deadline )); do
    if ssh $SSH_OPTS "$CONTROL_PLANE" "ssh $SSH_OPTS $target true" >/dev/null 2>&1; then
      ok "$host is reachable"
      return 0
    fi
    sleep "$WOL_POLL_INTERVAL"
  done
  die "Timed out waiting for $host"
}

wait_for_node_ready() {
  local host="$1"
  local deadline=$(( $(date +%s) + REBOOT_WAIT_SECONDS ))
  info "Waiting for $host to rejoin cluster as Ready (max ${REBOOT_WAIT_SECONDS}s)..."
  sleep 20  # give node time to go down before polling
  while (( $(date +%s) < deadline )); do
    local node_status
    node_status=$(kubectl_on_cp "get node $host --no-headers 2>/dev/null | awk '{print \$2}'" 2>/dev/null || true)
    if [[ "$node_status" == Ready* ]]; then
      ok "$host is Ready"
      return 0
    fi
    info "$host status: ${node_status:-unreachable}; waiting ${REBOOT_POLL_INTERVAL}s before retry"
    sleep "$REBOOT_POLL_INTERVAL"
  done
  die "Timed out waiting for $host to become Ready"
}

update_node() {
  local host="$1"
  info "Updating packages on $host..."
  run_on "$host" "echo 'Running apt-get update on $host'; sudo apt-get update && echo 'Running apt-get upgrade on $host'; sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -V && echo 'Running apt-get autoremove on $host'; sudo apt-get autoremove -y -V" | sed -u "s/^/[$host] /"
  ok "Packages updated on $host"
}

needs_reboot() {
  local host="$1"
  [[ "$(run_on "$host" "if [[ -f /var/run/reboot-required ]]; then echo yes; else echo no; fi")" == "yes" ]]
}

drain_node() {
  local host="$1"
  info "Cordoning and draining $host..."
  kubectl_on_cp "cordon $host"
  kubectl_on_cp "drain $host --ignore-daemonsets --delete-emptydir-data --timeout=300s --force" || warn "Drain had warnings on $host (continuing)"
  ok "$host drained"
}

uncordon_node() {
  local host="$1"
  info "Uncordoning $host..."
  kubectl_on_cp "uncordon $host"
  ok "$host uncordoned"
}

reboot_node() {
  local host="$1"
  info "Rebooting $host..."
  run_on "$host" "sudo reboot" || true  # ssh exits non-zero when connection drops
  ok "Reboot command sent to $host"
}

# ─── Step 1: Wake WoL nodes via homelab-amd ───────────────────────────────────
info "=== Step 1: Wake-on-LAN for desktop/GPU nodes ==="
for entry in "${WOL_MACS[@]}"; do
  node="${entry%%:*}"
  mac="${entry#*:}"
  info "Sending WoL packet to $node ($mac) via $CONTROL_PLANE..."
  ssh $SSH_OPTS "$CONTROL_PLANE" "wakeonlan $mac" 2>&1 | filter_ssh_banner
done

# ─── Step 2: Wait for WoL nodes ───────────────────────────────────────────────
info "=== Step 2: Waiting for WoL nodes to come online ==="
for node in "${WOL_NODES[@]}"; do
  wait_for_ssh "$node"
done

# ─── Step 3: Check what needs action and confirm ──────────────────────────────
collect_update_plan
confirm_update_plan

# ─── Step 4: Update & rolling reboot workers ──────────────────────────────────
info "=== Step 4: Update and rolling reboot worker nodes ==="
for node in "${ALL_WORKERS[@]}"; do
  if [[ "${PENDING_UPDATES[$node]:-0}" -eq 0 && "${REBOOT_REQUIRED[$node]:-no}" == "no" ]]; then
    info "Skipping $node: no pending updates and no reboot required"
    continue
  fi

  info "--- Processing worker: $node ---"
  if [[ "${PENDING_UPDATES[$node]:-0}" -gt 0 ]]; then
    update_node "$node"
  else
    info "No package updates pending for $node"
  fi

  if ! needs_reboot "$node"; then
    info "Skipping reboot for $node: no reboot required after update"
    continue
  fi

  drain_node "$node"
  reboot_node "$node"
  wait_for_node_ready "$node"
  uncordon_node "$node"
  info "Waiting 30s before next node..."
  sleep 30
done

# ─── Step 5: Update and reboot control plane ──────────────────────────────────
if [[ "${PENDING_UPDATES[$CONTROL_PLANE]:-0}" -eq 0 && "${REBOOT_REQUIRED[$CONTROL_PLANE]:-no}" == "no" ]]; then
  info "=== Step 5: Skipping control plane ($CONTROL_PLANE) — no pending updates and no reboot required ==="
else
  info "=== Step 5: Update and reboot control plane ($CONTROL_PLANE) ==="

if [[ "${PENDING_UPDATES[$CONTROL_PLANE]:-0}" -gt 0 ]]; then
  update_node "$CONTROL_PLANE"
else
  info "No package updates pending for $CONTROL_PLANE"
fi

if ! needs_reboot "$CONTROL_PLANE"; then
  info "Skipping reboot for $CONTROL_PLANE: no reboot required after update"
else

info "Rebooting $CONTROL_PLANE (control plane) — cluster will be unavailable briefly..."
# homelab-amd is local on the cluster; we ssh to it to reboot
ssh $SSH_OPTS "$CONTROL_PLANE" "sudo reboot" 2>&1 | filter_ssh_banner || true

info "Waiting for $CONTROL_PLANE to come back online (max ${REBOOT_WAIT_SECONDS}s)..."
sleep 30
deadline=$(( $(date +%s) + REBOOT_WAIT_SECONDS ))
while (( $(date +%s) < deadline )); do
  if ssh $SSH_OPTS "$CONTROL_PLANE" "sudo KUBECONFIG=$KUBECONFIG_PATH kubectl get nodes --no-headers 2>/dev/null | grep -q $CONTROL_PLANE" 2>/dev/null; then
    ok "$CONTROL_PLANE is back and k3s is running"
    break
  fi
  sleep "$REBOOT_POLL_INTERVAL"
done
fi
fi

# ─── Final status ─────────────────────────────────────────────────────────────
info "=== Final cluster status ==="
kubectl_on_cp "get nodes -o wide"
ok "All nodes updated and rebooted successfully."
