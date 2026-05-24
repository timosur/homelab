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
  --list-updates List available stable k3s updates and suggested upgrade path
  --plan-upgrade Suggest a safe upgrade path, ask for approval, and run it step by step
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
TMP_FILES=()

AUTO_YES="false"
SKIP_BACKUP="false"
PREFLIGHT_ONLY="false"
LIST_UPDATES="false"
PLAN_UPGRADE="false"

cleanup() {
  if [[ ${#TMP_FILES[@]} -gt 0 ]]; then
    rm -f "${TMP_FILES[@]}"
  fi
}

make_temp() {
  local tmp_file
  tmp_file=$(mktemp)
  TMP_FILES+=("$tmp_file")
  printf '%s\n' "$tmp_file"
}

trap cleanup EXIT

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
    --list-updates)
      LIST_UPDATES="true"
      ;;
    --plan-upgrade)
      PLAN_UPGRADE="true"
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

if [[ "$LIST_UPDATES" == "true" ]]; then
  check_dep curl
  check_dep python3
  check_dep sort
  check_dep wc
  check_dep mktemp
fi

if [[ "$PLAN_UPGRADE" == "true" ]]; then
  check_dep curl
  check_dep python3
  check_dep sort
  check_dep wc
  check_dep mktemp
fi

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

build_upgrade_plan() {
  local node_versions control_plane_version distinct_version_count release_file node_file plan_file

  log_info "Collecting current cluster versions" >&2
  node_versions=$(kubectl get nodes -o custom-columns=NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion --no-headers)
  control_plane_version=$(kubectl get nodes -l node-role.kubernetes.io/control-plane -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}')

  if [[ -z "$control_plane_version" ]]; then
    control_plane_version=$(printf '%s\n' "$node_versions" | awk 'NR == 1 { print $2 }')
    log_warning "Could not identify the control-plane node label; using the first node version as baseline" >&2
  fi

  distinct_version_count=$(printf '%s\n' "$node_versions" | awk '{print $2}' | sort -u | wc -l | tr -d ' ')
  release_file=$(make_temp)
  node_file=$(make_temp)
  plan_file=$(make_temp)

  printf '%s\n' "$node_versions" > "$node_file"

  log_info "Fetching stable k3s releases from GitHub" >&2
  curl -fsSL "https://api.github.com/repos/k3s-io/k3s/releases?per_page=100" > "$release_file"

  python3 - "$control_plane_version" "$TARGET_VERSION" "$distinct_version_count" "$node_file" "$release_file" "$plan_file" <<'PY'
import json
import re
import sys

current = sys.argv[1]
target = sys.argv[2]
distinct_count = sys.argv[3]
node_file = sys.argv[4]
release_file = sys.argv[5]
plan_file = sys.argv[6]

pattern = re.compile(r'^v(\d+)\.(\d+)\.(\d+)\+k3s(\d+)$')


def parse(version: str):
  match = pattern.match(version)
  if not match:
    return None
  return tuple(int(part) for part in match.groups())


def fmt(version: str) -> str:
  parsed = parse(version)
  if not parsed:
    return version
  major, minor, patch, build = parsed
  return f"v{major}.{minor}.{patch}+k3s{build}"


with open(release_file, 'r', encoding='utf-8') as handle:
  releases = json.load(handle)

node_versions = []
with open(node_file, 'r', encoding='utf-8') as handle:
  for raw_line in handle:
    line = raw_line.strip()
    if not line:
      continue
    parts = line.split()
    if len(parts) < 2:
      continue
    node_versions.append({"name": parts[0], "version": parts[1]})

stable = []
for release in releases:
  tag = release.get('tag_name', '')
  if release.get('draft') or release.get('prerelease'):
    continue
  parsed = parse(tag)
  if not parsed:
    continue
  stable.append((parsed, tag))

stable.sort()

current_parsed = parse(current)
target_parsed = parse(target)

if not current_parsed:
  print(f"[ERROR] Could not parse current cluster version: {current}")
  sys.exit(1)

if not target_parsed:
  print(f"[ERROR] Could not parse target inventory version: {target}")
  sys.exit(1)

major = current_parsed[0]
latest_by_minor = {}
for parsed, tag in stable:
  if parsed[0] != major:
    continue
  latest_by_minor[(parsed[0], parsed[1])] = tag

available_minors = sorted(minor for maj, minor in latest_by_minor if maj == major and minor >= current_parsed[1])


def build_path(start, end, final_version):
  if end <= start:
    return []
  if end[0] != start[0]:
    return None

  steps = []
  current_minor_latest = latest_by_minor.get((start[0], start[1]))
  if end[1] > start[1] and current_minor_latest and parse(current_minor_latest) > start:
    steps.append(current_minor_latest)

  for minor in range(start[1] + 1, end[1]):
    tag = latest_by_minor.get((start[0], minor))
    if not tag:
      return None
    steps.append(tag)

  if not steps or steps[-1] != final_version:
    steps.append(final_version)
  return steps


path_to_configured = []
if target_parsed > current_parsed:
  path_to_configured = build_path(current_parsed, target_parsed, target)

latest_available_minor = available_minors[-1] if available_minors else None
latest_available = None
latest_available_path = []
if latest_available_minor is not None:
  latest_available = latest_by_minor[(major, latest_available_minor)]
  latest_available_parsed = parse(latest_available)
  if latest_available_parsed > current_parsed:
    latest_available_path = build_path(current_parsed, latest_available_parsed, latest_available)

selected_mode = "none"
selected_target = None
selected_path = []
selected_message = "No upgrade plan is needed."

if target_parsed > current_parsed:
  selected_mode = "configured-target"
  selected_target = target
  selected_path = path_to_configured
  selected_message = "Using the configured inventory target."
elif latest_available_path:
  selected_mode = "latest-stable"
  selected_target = latest_available
  selected_path = latest_available_path
  selected_message = "Inventory target is not newer than the cluster; using the newest fetched stable release instead."

available_by_minor = []
for minor in available_minors:
  available_by_minor.append({
    "minor": f"v{major}.{minor}",
    "version": latest_by_minor[(major, minor)],
    "is_current_minor": minor == current_parsed[1],
    "is_target_minor": minor == target_parsed[1],
  })

plan = {
  "node_versions": node_versions,
  "current": current,
  "configured_target": target,
  "control_plane_baseline": current,
  "mixed_versions": distinct_count != "1",
  "available_by_minor": available_by_minor,
  "path_to_configured": path_to_configured,
  "latest_available": latest_available,
  "path_to_latest": latest_available_path,
  "selected_mode": selected_mode,
  "selected_target": selected_target,
  "selected_path": selected_path,
  "selected_message": selected_message,
}

with open(plan_file, 'w', encoding='utf-8') as handle:
  json.dump(plan, handle)
PY

  printf '%s\n' "$plan_file"
}

render_upgrade_plan() {
  local plan_file="$1"

  python3 - "$plan_file" <<'PY'
import json
import re
import sys

pattern = re.compile(r'^v(\d+)\.(\d+)\.(\d+)\+k3s(\d+)$')

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
  plan = json.load(handle)


def parse(version: str):
  match = pattern.match(version)
  if not match:
    return None
  return tuple(int(part) for part in match.groups())


def fmt(version: str) -> str:
  parsed = parse(version)
  if not parsed:
    return version
  major, minor, patch, build = parsed
  return f"v{major}.{minor}.{patch}+k3s{build}"


print("")
print("Cluster node versions:")
for node in plan["node_versions"]:
  print(f"{node['name']:<22} {node['version']}")

print("")
print(f"Configured inventory target: {plan['configured_target']}")
print(f"Control-plane baseline:     {plan['control_plane_baseline']}")

if plan["mixed_versions"]:
  print("")
  print("[WARNING] Cluster is on mixed k3s versions; suggested path uses the control-plane version as baseline")

print("")
print("Available stable releases by minor:")
if not plan["available_by_minor"]:
  print("  No releases found for the current major version.")
else:
  for item in plan["available_by_minor"]:
    marker = []
    if item["is_current_minor"]:
      marker.append("current minor")
    if item["is_target_minor"]:
      marker.append("target minor")
    suffix = f" ({', '.join(marker)})" if marker else ""
    print(f"  {item['minor']}: {fmt(item['version'])}{suffix}")

print("")
print("Suggested path to configured target:")
configured_target = parse(plan["configured_target"])
current = parse(plan["current"])
if configured_target is None or current is None or configured_target <= current:
  print("  Inventory target is not newer than the current cluster version.")
elif plan["path_to_configured"] is None:
  print("  Could not build a complete minor-by-minor path from the fetched releases.")
else:
  for index, version in enumerate(plan["path_to_configured"], start=1):
    print(f"  {index}. {fmt(version)}")

print("")
print("Suggested path to newest fetched stable release:")
if not plan["path_to_latest"]:
  print("  Cluster is already on the newest fetched stable release for this major version.")
else:
  for index, version in enumerate(plan["path_to_latest"], start=1):
    print(f"  {index}. {fmt(version)}")

print("")
print("Selected execution plan:")
if plan["selected_mode"] == "none" or not plan["selected_path"]:
  print(f"  {plan['selected_message']}")
else:
  print(f"  {plan['selected_message']}")
  for index, version in enumerate(plan["selected_path"], start=1):
    print(f"  {index}. {fmt(version)}")

print("")
print("Note: k3s follows the Kubernetes version skew policy; do not skip intermediate minor versions.")
PY
}

list_updates() {
  local plan_file
  plan_file=$(build_upgrade_plan)
  render_upgrade_plan "$plan_file"
}

set_inventory_target() {
  local next_version="$1"

  python3 - "$INVENTORY_FILE" "$next_version" <<'PY'
import re
import sys

inventory_file = sys.argv[1]
next_version = sys.argv[2]

with open(inventory_file, 'r', encoding='utf-8') as handle:
  content = handle.read()

updated, count = re.subn(r'(^\s*k3s_version:\s*).+$', rf'\1{next_version}', content, count=1, flags=re.MULTILINE)
if count != 1:
  print(f"Could not update k3s_version in {inventory_file}", file=sys.stderr)
  sys.exit(1)

with open(inventory_file, 'w', encoding='utf-8') as handle:
  handle.write(updated)
PY

  TARGET_VERSION="$next_version"
}

plan_upgrade() {
  local plan_file selected_mode selected_count step_index step_version
  local -a selected_steps=()

  plan_file=$(build_upgrade_plan)
  render_upgrade_plan "$plan_file"

  selected_mode=$(python3 - "$plan_file" <<'PY'
import json
import sys

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
  plan = json.load(handle)

print(plan['selected_mode'])
PY
)

  mapfile -t selected_steps < <(python3 - "$plan_file" <<'PY'
import json
import sys

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
  plan = json.load(handle)

for version in plan['selected_path']:
  print(version)
PY
)

  if [[ "$selected_mode" == "none" || ${#selected_steps[@]} -eq 0 ]]; then
    log_info "No executable upgrade plan was selected"
    exit 0
  fi

  echo ""
  log_info "The inventory target will be updated before each rollout step"
  if ! confirm "Approve this upgrade plan and run it now?"; then
    log_warning "Aborted before changing the inventory target"
    exit 0
  fi

  if [[ "$SKIP_BACKUP" != "true" ]]; then
    if confirm "Run the backup playbook before starting the upgrade plan?"; then
      run_backup
    else
      log_warning "Skipping backup at your request"
    fi
  else
    log_warning "Skipping backup because --skip-backup was provided"
  fi

  selected_count=${#selected_steps[@]}
  for step_index in "${!selected_steps[@]}"; do
    step_version="${selected_steps[$step_index]}"
    echo ""
    log_info "Executing plan step $((step_index + 1))/${selected_count}: ${step_version}"
    set_inventory_target "$step_version"
    log_info "Updated inventory target to ${step_version}"
    run_update
    log_info "Cluster nodes after step $((step_index + 1))"
    kubectl get nodes -o wide
  done
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

if [[ "$LIST_UPDATES" == "true" ]]; then
  list_updates
  exit 0
fi

if [[ "$PLAN_UPGRADE" == "true" ]]; then
  run_preflight
  plan_upgrade
  print_follow_up
  exit 0
fi

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