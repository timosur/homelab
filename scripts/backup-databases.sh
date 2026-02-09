#!/bin/bash
#
# Database Backup Script for Homelab Applications
# Creates local backups of all application databases
#
# Supported databases:
# - PostgreSQL (CloudNativePG): mealie, open-webui, paperless
# - SQLite: garden, actual
#

set -euo pipefail

# Configuration
BACKUP_BASE_DIR="${BACKUP_BASE_DIR:-$HOME/homelab-backups}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="${BACKUP_BASE_DIR}/${TIMESTAMP}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"  # Number of days to keep backups

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Create backup directory
create_backup_dir() {
    log_info "Creating backup directory: ${BACKUP_DIR}"
    mkdir -p "${BACKUP_DIR}"
}

# Backup PostgreSQL database using CloudNativePG
# Uses pg_dump from the primary pod
backup_cnpg_postgres() {
    local namespace="$1"
    local cluster_name="$2"
    local database="$3"
    local backup_name="${namespace}-postgres-${TIMESTAMP}.sql.gz"
    
    log_info "Backing up PostgreSQL database: ${namespace}/${cluster_name} (database: ${database})"
    
    # Get the primary pod name
    local primary_pod
    primary_pod=$(kubectl get pods -n "${namespace}" -l "cnpg.io/cluster=${cluster_name},cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    
    if [ -z "${primary_pod}" ]; then
        log_error "Could not find primary pod for ${cluster_name} in namespace ${namespace}"
        return 1
    fi
    
    log_info "Found primary pod: ${primary_pod}"
    
    # Execute pg_dump and compress
    if kubectl exec -n "${namespace}" "${primary_pod}" -- \
        pg_dump -U postgres -d "${database}" --format=plain --clean --if-exists 2>/dev/null | \
        gzip > "${BACKUP_DIR}/${backup_name}"; then
        
        local size
        size=$(du -h "${BACKUP_DIR}/${backup_name}" | cut -f1)
        log_success "PostgreSQL backup completed: ${backup_name} (${size})"
        return 0
    else
        log_error "Failed to backup PostgreSQL database: ${namespace}/${cluster_name}"
        rm -f "${BACKUP_DIR}/${backup_name}"
        return 1
    fi
}

# Backup SQLite database by copying from pod
backup_sqlite() {
    local namespace="$1"
    local app_label="$2"
    local container="${3:-}"  # Optional container name
    local db_path="$4"
    local backup_name="${namespace}-sqlite-${TIMESTAMP}.db"
    
    log_info "Backing up SQLite database: ${namespace} (path: ${db_path})"
    
    # Get the pod name
    local pod_name
    pod_name=$(kubectl get pods -n "${namespace}" -l "app=${app_label}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    
    if [ -z "${pod_name}" ]; then
        log_error "Could not find pod with label app=${app_label} in namespace ${namespace}"
        return 1
    fi
    
    log_info "Found pod: ${pod_name}"
    
    # Build container args if specified
    local container_args=""
    if [ -n "${container}" ]; then
        container_args="-c ${container}"
    fi
    
    # Check if database file exists
    if ! kubectl exec -n "${namespace}" "${pod_name}" ${container_args} -- test -f "${db_path}" 2>/dev/null; then
        log_warning "Database file not found at ${db_path} in ${pod_name}"
        return 1
    fi
    
    # Create a safe backup using SQLite's backup command if available, otherwise copy
    # First try using sqlite3 .backup command for consistency
    if kubectl exec -n "${namespace}" "${pod_name}" ${container_args} -- \
        sh -c "command -v sqlite3 >/dev/null 2>&1" 2>/dev/null; then
        
        log_info "Using SQLite backup command for consistent backup..."
        local temp_backup="/tmp/backup-${TIMESTAMP}.db"
        
        if kubectl exec -n "${namespace}" "${pod_name}" ${container_args} -- \
            sqlite3 "${db_path}" ".backup '${temp_backup}'" 2>/dev/null; then
            
            kubectl cp "${namespace}/${pod_name}:${temp_backup}" "${BACKUP_DIR}/${backup_name}" ${container_args}
            kubectl exec -n "${namespace}" "${pod_name}" ${container_args} -- rm -f "${temp_backup}" 2>/dev/null || true
        else
            log_warning "SQLite backup command failed, falling back to file copy"
            kubectl cp "${namespace}/${pod_name}:${db_path}" "${BACKUP_DIR}/${backup_name}" ${container_args}
        fi
    else
        # Fallback to direct file copy
        log_info "sqlite3 not available, copying database file directly..."
        kubectl cp "${namespace}/${pod_name}:${db_path}" "${BACKUP_DIR}/${backup_name}" ${container_args}
    fi
    
    if [ -f "${BACKUP_DIR}/${backup_name}" ]; then
        # Compress the SQLite backup
        gzip "${BACKUP_DIR}/${backup_name}"
        local size
        size=$(du -h "${BACKUP_DIR}/${backup_name}.gz" | cut -f1)
        log_success "SQLite backup completed: ${backup_name}.gz (${size})"
        return 0
    else
        log_error "Failed to backup SQLite database: ${namespace}"
        return 1
    fi
}

# Backup Actual Budget (stores data in /data directory, includes SQLite files)
backup_actual() {
    local namespace="actual"
    local backup_name="actual-data-${TIMESTAMP}.tar.gz"
    
    log_info "Backing up Actual Budget data"
    
    # Get the pod name
    local pod_name
    pod_name=$(kubectl get pods -n "${namespace}" -l "app=actual" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    
    if [ -z "${pod_name}" ]; then
        log_error "Could not find Actual pod"
        return 1
    fi
    
    log_info "Found pod: ${pod_name}"
    
    # Create tar archive of the data directory and stream it
    if kubectl exec -n "${namespace}" "${pod_name}" -- \
        tar -czf - -C /data . 2>/dev/null > "${BACKUP_DIR}/${backup_name}"; then
        
        local size
        size=$(du -h "${BACKUP_DIR}/${backup_name}" | cut -f1)
        log_success "Actual Budget backup completed: ${backup_name} (${size})"
        return 0
    else
        log_error "Failed to backup Actual Budget data"
        rm -f "${BACKUP_DIR}/${backup_name}"
        return 1
    fi
}

# Cleanup old backups
cleanup_old_backups() {
    log_info "Cleaning up backups older than ${RETAIN_DAYS} days..."
    
    if [ -d "${BACKUP_BASE_DIR}" ]; then
        local count
        count=$(find "${BACKUP_BASE_DIR}" -maxdepth 1 -type d -mtime +${RETAIN_DAYS} | wc -l | tr -d ' ')
        
        if [ "${count}" -gt 0 ]; then
            find "${BACKUP_BASE_DIR}" -maxdepth 1 -type d -mtime +${RETAIN_DAYS} -exec rm -rf {} \;
            log_success "Removed ${count} old backup(s)"
        else
            log_info "No old backups to clean up"
        fi
    fi
}

# Print backup summary
print_summary() {
    echo ""
    echo "=========================================="
    echo "           BACKUP SUMMARY"
    echo "=========================================="
    echo ""
    echo "Backup Location: ${BACKUP_DIR}"
    echo "Timestamp: ${TIMESTAMP}"
    echo ""
    
    if [ -d "${BACKUP_DIR}" ]; then
        echo "Files created:"
        ls -lh "${BACKUP_DIR}" 2>/dev/null | tail -n +2 | awk '{print "  " $9 " (" $5 ")"}'
        echo ""
        local total_size
        total_size=$(du -sh "${BACKUP_DIR}" 2>/dev/null | cut -f1)
        echo "Total size: ${total_size}"
    fi
    
    echo ""
    echo "=========================================="
}

# Main backup function
main() {
    echo ""
    echo "=========================================="
    echo "    Homelab Database Backup Script"
    echo "=========================================="
    echo ""
    
    # Check if kubectl is available
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl is not installed or not in PATH"
        exit 1
    fi
    
    # Check kubernetes connection
    if ! kubectl cluster-info &> /dev/null; then
        log_error "Cannot connect to Kubernetes cluster"
        exit 1
    fi
    
    log_success "Connected to Kubernetes cluster"
    
    create_backup_dir
    
    local failed=0
    local success=0
    
    echo ""
    echo "--- PostgreSQL Databases (CloudNativePG) ---"
    echo ""
    
    # Backup Mealie PostgreSQL
    if backup_cnpg_postgres "mealie" "mealie-postgres" "mealie"; then
        ((success++))
    else
        ((failed++))
    fi
    
    # Backup Open-WebUI PostgreSQL
    if backup_cnpg_postgres "open-webui" "open-webui-postgres" "openwebui"; then
        ((success++))
    else
        ((failed++))
    fi
    
    # Backup Paperless PostgreSQL
    if backup_cnpg_postgres "paperless" "paperless-postgres" "paperless"; then
        ((success++))
    else
        ((failed++))
    fi
    
    echo ""
    echo "--- SQLite Databases ---"
    echo ""
    
    # Backup Garden SQLite
    if backup_sqlite "garden" "garden-backend" "garden-backend" "/app/db/garden.db"; then
        ((success++))
    else
        ((failed++))
    fi
    
    echo ""
    echo "--- File-based Data ---"
    echo ""
    
    # Backup Actual Budget
    if backup_actual; then
        ((success++))
    else
        ((failed++))
    fi
    
    # Cleanup old backups
    echo ""
    cleanup_old_backups
    
    # Print summary
    print_summary
    
    echo "Results: ${success} succeeded, ${failed} failed"
    echo ""
    
    if [ ${failed} -gt 0 ]; then
        log_warning "Some backups failed. Check the logs above for details."
        exit 1
    else
        log_success "All backups completed successfully!"
        exit 0
    fi
}

# Run main function
main "$@"
