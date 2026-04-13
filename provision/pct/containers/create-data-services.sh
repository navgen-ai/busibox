#!/usr/bin/env bash
#
# Create Data Services LXC Containers
#
# Description:
#   Creates data layer containers: PostgreSQL, Milvus, and MinIO.
#   These containers handle persistent data storage for the platform.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-data-services.sh [staging|production]
#
# Containers Created:
#   - pg-lxc     - PostgreSQL database
#   - milvus-lxc - Milvus vector database
#   - files-lxc  - MinIO S3-compatible object storage
#
# Notes:
#   - All containers require persistent storage mounts
#   - Host paths must exist before running (setup-proxmox-host.sh)

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "staging" ]]; then
  echo "==> Creating data services in STAGING mode"
  source "${PCT_DIR}/stage-vars.env"
  PREFIX="${STAGE_PREFIX}"
  
  CT_PG="$CT_PG_STAGING"
  CT_MILVUS="$CT_MILVUS_STAGING"
  CT_FILES="$CT_FILES_STAGING"
  
  IP_PG="$IP_PG_STAGING"
  IP_MILVUS="$IP_MILVUS_STAGING"
  IP_FILES="$IP_FILES_STAGING"
else
  echo "==> Creating data services in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  PREFIX=""
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

# Use environment-specific data directories to isolate staging from production.
# Without this, both environments mount the same host paths and share state
# (e.g. MinIO bakes credentials into its data dir on first start, so a shared
# dir means whichever env starts first wins and the other gets mismatched creds).
if [[ "$MODE" == "staging" ]]; then
  DATA_BASE="/var/lib/data-staging"
else
  DATA_BASE="/var/lib/data"
fi

# Track only NEWLY created containers for cleanup on error.
# Pre-existing containers must never be touched.
CREATED_CONTAINERS=()

cleanup_on_error() {
  if [[ ${#CREATED_CONTAINERS[@]} -eq 0 ]]; then
    echo "No newly created containers to clean up."
    exit 1
  fi
  echo ""
  echo "=========================================="
  echo "Error occurred - cleaning up newly created containers only"
  echo "=========================================="
  for ctid in "${CREATED_CONTAINERS[@]}"; do
    if pct status "$ctid" &>/dev/null; then
      echo "Removing newly created container $ctid..."
      pct stop "$ctid" 2>/dev/null || true
      sleep 2
      pct destroy "$ctid" --purge 2>/dev/null || true
    fi
  done
  echo "Cleanup complete"
  exit 1
}

# Helper: create container and track only if it was newly created
create_and_track() {
  local ctid="$1"
  local existed=false
  pct status "$ctid" &>/dev/null && existed=true

  create_ct "$@" || cleanup_on_error

  if ! $existed; then
    CREATED_CONTAINERS+=("$ctid")
  fi
}

# Create PostgreSQL container
create_and_track "$CT_PG" "$IP_PG" "${PREFIX}pg-lxc" unpriv
add_data_mount "$CT_PG" "${DATA_BASE}/postgres" "/var/lib/postgresql/data" "0"

# Create Milvus container (privileged for better performance)
create_and_track "$CT_MILVUS" "$IP_MILVUS" "${PREFIX}milvus-lxc" priv
add_data_mount "$CT_MILVUS" "${DATA_BASE}/milvus" "/srv/milvus/data" "0"

# Configure Milvus container for Docker sysctls support
# Docker containers inside need permission to set network sysctls
CONFIG_FILE="/etc/pve/lxc/${CT_MILVUS}.conf"
if ! grep -q "# Docker sysctls support" "$CONFIG_FILE"; then
  echo "    Configuring Docker sysctls support for Milvus..."
  pct stop "$CT_MILVUS"
  sleep 2
  cat >> "$CONFIG_FILE" << 'EOF'
# Docker sysctls support - allow nested Docker containers to set network sysctls
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw
EOF
  pct start "$CT_MILVUS"
  sleep 3
  echo "    Docker sysctls support configured"
fi

# Create MinIO container (privileged for storage access and Docker sysctls)
create_and_track "$CT_FILES" "$IP_FILES" "${PREFIX}files-lxc" priv
add_data_mount "$CT_FILES" "${DATA_BASE}/minio" "/srv/minio/data" "0"

# Configure sysctls support for Docker-in-LXC (MinIO container needs to set sysctls)
echo "    Configuring Docker sysctl support for ${PREFIX}files-lxc..."
pct stop "$CT_FILES" 2>/dev/null || true
sleep 2
cat >> "/etc/pve/lxc/${CT_FILES}.conf" << EOF
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw
EOF
pct start "$CT_FILES"
sleep 3
echo "    Docker sysctls support configured"

echo ""
echo "=========================================="
echo "Data services created successfully!"
echo "Mode: ${MODE}"
echo "Containers:"
echo "  - ${PREFIX}pg-lxc:     $CT_PG @ $IP_PG"
echo "  - ${PREFIX}milvus-lxc: $CT_MILVUS @ $IP_MILVUS"
echo "  - ${PREFIX}files-lxc:  $CT_FILES @ $IP_FILES"
echo "=========================================="

