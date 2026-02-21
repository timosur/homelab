#!/bin/bash
#
# Homelab Restore Script (Manifest-driven)
#
# Reads manifest.json from a backup directory to determine what can be restored.
# The manifest is created automatically by the cluster-backup Ansible role.
#
# Backups are created nightly via:
#   cd ansible && ansible-playbook playbooks/cluster-backup.yml -i inventory.yml
#
# Usage:
#   ./restore-homelab.sh <backup-dir>                # Interactive mode
#   ./restore-homelab.sh <backup-dir> --k3s          # Restore k3s state only
#   ./restore-homelab.sh <backup-dir> --databases    # Restore all CNPG databases
#   ./restore-homelab.sh <backup-dir> --pvcs         # Restore all PVC volumes
#   ./restore-homelab.sh <backup-dir> --all          # Restore everything
#
# Examples:
#   ./restore-homelab.sh /mnt/synology-home/Backup/homelab-backups/20260221-060003
#   ./restore-homelab.sh /mnt/synology-home/Backup/homelab-backups/20260221-060003 --k3s
#

set -euo pipefail

# ──────────────────────────────────────────────────────
# Colors and logging
# ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

confirm() {
    local msg="$1"
    echo -e -n "${YELLOW}[CONFIRM]${NC} ${msg} [y/N] "
    read -r answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

# ──────────────────────────────────────────────────────
# Manifest helpers
# ──────────────────────────────────────────────────────
validate_manifest() {
    local manifest="$1"

    if [ ! -f "$manifest" ]; then
        log_error "No manifest.json found in backup directory"
        log_error "This backup may have been created with an older script version"
        exit 1
    fi

    local version
    version=$(jq -r '.version' "$manifest" 2>/dev/null)

    if [ "$version" != "1" ]; then
        log_error "Unsupported manifest version: ${version}"
        exit 1
    fi
}

show_backup_summary() {
    local manifest="$1"

    echo "Backup contents (from manifest.json):"
    echo ""

    local ts
    ts=$(jq -r '.timestamp' "$manifest")
    local cp
    cp=$(jq -r '.control_plane' "$manifest")
    echo "  Timestamp:     ${ts}"
    echo "  Control plane: ${cp}"
    echo ""

    # k3s state
    local k3s_file
    k3s_file=$(jq -r '.k3s_state.file // empty' "$manifest")
    if [ -n "$k3s_file" ]; then
        echo "  k3s state:"
        echo "    ${k3s_file}"
    fi

    # CNPG databases
    local db_count
    db_count=$(jq '.cnpg_databases | length' "$manifest")
    if [ "$db_count" -gt 0 ]; then
        echo ""
        echo "  CNPG databases (${db_count}):"
        jq -r '.cnpg_databases[] | "    \(.namespace)/\(.cluster) (db: \(.database))"' "$manifest"
    fi

    # PVCs
    local pvc_count
    pvc_count=$(jq '.pvc_volumes | length' "$manifest")
    if [ "$pvc_count" -gt 0 ]; then
        echo ""
        echo "  PVC volumes (${pvc_count}):"
        jq -r '.pvc_volumes[] | "    \(.namespace)/\(.pvc) on \(.node)"' "$manifest"
    fi

    echo ""
}

# ──────────────────────────────────────────────────────
# Restore k3s cluster state
# ──────────────────────────────────────────────────────
restore_k3s_state() {
    local backup_dir="$1" manifest="$2"

    local k3s_file
    k3s_file=$(jq -r '.k3s_state.file // empty' "$manifest")

    if [ -z "$k3s_file" ]; then
        log_warning "No k3s state in this backup"
        return 1
    fi

    local archive="${backup_dir}/${k3s_file}"
    if [ ! -f "$archive" ]; then
        log_error "k3s state file not found: ${k3s_file}"
        return 1
    fi

    local cp
    cp=$(jq -r '.control_plane' "$manifest")

    echo ""
    echo "  This will:"
    echo "    1. Stop k3s on ${cp}"
    echo "    2. Replace state.db, token, TLS certs, manifests, and config"
    echo "    3. Start k3s again"
    echo ""
    echo "  Worker nodes will automatically reconnect."
    echo ""

    if ! confirm "Restore k3s cluster state on ${cp}? THIS WILL REPLACE ALL CLUSTER STATE."; then
        log_info "Skipping k3s state restore"
        return 0
    fi

    log_info "Uploading backup to ${cp}..."
    scp "$archive" "${cp}:/tmp/k3s-restore.tar.gz"

    log_info "Stopping k3s, restoring state, restarting..."
    ssh "$cp" "
        set -e

        # Safety backup of current state
        SAFETY_DIR=/tmp/k3s-pre-restore-\$(date +%Y%m%d-%H%M%S)
        sudo mkdir -p \${SAFETY_DIR}
        sudo cp /var/lib/rancher/k3s/server/db/state.db \${SAFETY_DIR}/ 2>/dev/null || true
        sudo cp /var/lib/rancher/k3s/server/token \${SAFETY_DIR}/ 2>/dev/null || true
        sudo cp -r /var/lib/rancher/k3s/server/tls \${SAFETY_DIR}/ 2>/dev/null || true
        echo \"Safety backup saved to \${SAFETY_DIR}\"

        sudo systemctl stop k3s
        sleep 3

        RESTORE_DIR=/tmp/k3s-restore-extract
        sudo rm -rf \${RESTORE_DIR}
        sudo mkdir -p \${RESTORE_DIR}
        sudo tar -xzf /tmp/k3s-restore.tar.gz -C \${RESTORE_DIR}

        sudo cp \${RESTORE_DIR}/state.db /var/lib/rancher/k3s/server/db/state.db
        sudo cp \${RESTORE_DIR}/token /var/lib/rancher/k3s/server/token
        sudo cp -r \${RESTORE_DIR}/tls/* /var/lib/rancher/k3s/server/tls/
        sudo cp -r \${RESTORE_DIR}/manifests/* /var/lib/rancher/k3s/server/manifests/
        sudo cp \${RESTORE_DIR}/config.yaml /etc/rancher/k3s/config.yaml

        sudo chmod 600 /var/lib/rancher/k3s/server/token
        sudo chmod 600 /var/lib/rancher/k3s/server/db/state.db

        sudo rm -rf \${RESTORE_DIR} /tmp/k3s-restore.tar.gz
        sudo systemctl start k3s
    "

    if [ $? -eq 0 ]; then
        log_success "k3s state restored"
        log_info "Waiting for k3s to become ready..."
        sleep 10
        ssh "$cp" "sudo k3s kubectl get nodes 2>/dev/null" || \
            log_warning "k3s not yet ready — check: ssh ${cp} sudo k3s kubectl get nodes"
        return 0
    else
        log_error "k3s restore failed — check ${cp} manually"
        log_info "Safety backup in /tmp/k3s-pre-restore-* on the node"
        return 1
    fi
}

# ──────────────────────────────────────────────────────
# Restore CNPG databases (iterates manifest entries)
# ──────────────────────────────────────────────────────
restore_cnpg_databases() {
    local backup_dir="$1" manifest="$2"

    local count
    count=$(jq '.cnpg_databases | length' "$manifest")

    if [ "$count" -eq 0 ]; then
        log_info "No CNPG databases in this backup"
        return 0
    fi

    local any_failed=0

    for i in $(seq 0 $((count - 1))); do
        local ns cluster database file
        ns=$(jq -r ".cnpg_databases[$i].namespace" "$manifest")
        cluster=$(jq -r ".cnpg_databases[$i].cluster" "$manifest")
        database=$(jq -r ".cnpg_databases[$i].database" "$manifest")
        file=$(jq -r ".cnpg_databases[$i].file" "$manifest")

        local archive="${backup_dir}/${file}"
        if [ ! -f "$archive" ]; then
            log_error "Backup file not found: ${file}"
            ((any_failed++))
            continue
        fi

        log_info "Found: ${file}"

        if ! confirm "Restore ${ns}/${database}? THIS WILL REPLACE ALL DATA."; then
            log_info "Skipping ${ns}/${database}"
            continue
        fi

        local primary_pod
        primary_pod=$(kubectl get pods -n "$ns" \
            -l "cnpg.io/cluster=${cluster},cnpg.io/instanceRole=primary" \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

        if [ -z "$primary_pod" ]; then
            log_error "No primary pod for ${cluster} in ${ns}"
            ((any_failed++))
            continue
        fi

        log_info "Restoring to pod: ${primary_pod}"

        if gunzip -c "$archive" | kubectl exec -i -n "$ns" "$primary_pod" -- \
            psql -U postgres -d "$database" 2>/dev/null; then
            log_success "Restored: ${ns}/${database}"
        else
            log_error "Failed: ${ns}/${database}"
            ((any_failed++))
        fi
    done

    return $any_failed
}

# ──────────────────────────────────────────────────────
# Restore PVC volumes (iterates manifest entries, streams via SSH)
# ──────────────────────────────────────────────────────
restore_pvc_volumes() {
    local backup_dir="$1" manifest="$2"

    local count
    count=$(jq '.pvc_volumes | length' "$manifest")

    if [ "$count" -eq 0 ]; then
        log_info "No PVC volumes in this backup"
        return 0
    fi

    local any_failed=0

    for i in $(seq 0 $((count - 1))); do
        local ns pvc node path file
        ns=$(jq -r ".pvc_volumes[$i].namespace" "$manifest")
        pvc=$(jq -r ".pvc_volumes[$i].pvc" "$manifest")
        node=$(jq -r ".pvc_volumes[$i].node" "$manifest")
        path=$(jq -r ".pvc_volumes[$i].path" "$manifest")
        file=$(jq -r ".pvc_volumes[$i].file" "$manifest")

        local archive="${backup_dir}/${file}"
        if [ ! -f "$archive" ]; then
            log_error "Backup file not found: ${file}"
            ((any_failed++))
            continue
        fi

        log_info "Found: ${file}"
        log_warning "Ensure pods using PVC ${ns}/${pvc} are scaled down before restoring"

        if ! confirm "Restore PVC ${ns}/${pvc} on ${node}? THIS WILL REPLACE ALL DATA."; then
            log_info "Skipping ${ns}/${pvc}"
            continue
        fi

        log_info "Restoring to ${node}:${path}..."

        if ssh "$node" "sudo rm -rf '${path}'/* 2>/dev/null; sudo mkdir -p '${path}' && sudo tar -xzf - -C '${path}'" \
            < "$archive" 2>/dev/null; then
            log_success "Restored: ${ns}/${pvc}"
        else
            log_error "Failed: ${ns}/${pvc}"
            ((any_failed++))
        fi
    done

    return $any_failed
}

# ──────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────
usage() {
    echo "Usage: $0 <backup-directory> [--k3s|--databases|--pvcs|--all]"
    echo ""
    echo "Options:"
    echo "  --k3s        Restore k3s cluster state only"
    echo "  --databases  Restore all CNPG databases"
    echo "  --pvcs       Restore all PVC volumes"
    echo "  --all        Restore everything"
    echo "  (no option)  Interactive mode"
    echo ""
    echo "Available backups:"
    local base="${BACKUP_BASE_DIR:-/mnt/synology-home/Backup/homelab-backups}"
    if [ -d "$base" ]; then
        ls -1d "$base"/*/ 2>/dev/null | while read -r d; do echo "  $(basename "$d")"; done
    fi
}

main() {
    echo ""
    echo "=========================================="
    echo "       Homelab Restore Script"
    echo "=========================================="
    echo ""

    if [ $# -lt 1 ]; then
        usage
        exit 1
    fi

    # Pre-flight checks
    for cmd in kubectl jq ssh scp; do
        if ! command -v "$cmd" &>/dev/null; then
            log_error "${cmd} is not installed or not in PATH"
            exit 1
        fi
    done

    local backup_dir="$1"
    local mode="${2:-interactive}"
    local manifest="${backup_dir}/manifest.json"

    if [ ! -d "$backup_dir" ]; then
        log_error "Backup directory not found: ${backup_dir}"
        usage
        exit 1
    fi

    validate_manifest "$manifest"
    show_backup_summary "$manifest"

    local failed=0
    local success=0

    case "$mode" in
        --k3s)
            if restore_k3s_state "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
            ;;

        --databases)
            if restore_cnpg_databases "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
            ;;

        --pvcs)
            if restore_pvc_volumes "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
            ;;

        --all)
            echo "--- k3s Cluster State ---"
            echo ""
            if restore_k3s_state "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
            echo ""
            echo "--- CNPG Databases ---"
            echo ""
            if restore_cnpg_databases "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
            echo ""
            echo "--- PVC Volumes ---"
            echo ""
            if restore_pvc_volumes "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
            ;;

        interactive|*)
            local db_count pvc_count
            db_count=$(jq '.cnpg_databases | length' "$manifest")
            pvc_count=$(jq '.pvc_volumes | length' "$manifest")

            echo "What would you like to restore?"
            echo ""
            echo "  1) k3s cluster state"
            echo "  2) CNPG databases (${db_count} found)"
            echo "  3) PVC volumes (${pvc_count} found)"
            echo "  4) Everything"
            echo "  5) Cancel"
            echo ""
            echo -n "Choice [1-5]: "
            read -r choice
            echo ""

            case "$choice" in
                1)
                    if restore_k3s_state "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
                    ;;
                2)
                    if restore_cnpg_databases "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
                    ;;
                3)
                    if restore_pvc_volumes "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
                    ;;
                4)
                    if restore_k3s_state "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
                    if restore_cnpg_databases "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
                    if restore_pvc_volumes "$backup_dir" "$manifest"; then ((success++)); else ((failed++)); fi
                    ;;
                5)
                    log_info "Cancelled"
                    exit 0
                    ;;
                *)
                    log_error "Invalid choice"
                    exit 1
                    ;;
            esac
            ;;
    esac

    echo ""
    echo "Results: ${success} succeeded, ${failed} failed"
    echo ""

    if [ "$failed" -gt 0 ]; then
        log_warning "Some restores failed — check the logs above."
        exit 1
    else
        log_success "All restores completed successfully!"
        exit 0
    fi
}

main "$@"
