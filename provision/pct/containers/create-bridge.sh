#!/usr/bin/env bash
#
# Create Bridge Service LXC Container
#
# Description:
#   Creates the bridge service container for multi-channel communication
#   (Signal, Email, WhatsApp, Webhooks).
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-bridge.sh [staging|production]
#
# Containers Created:
#   - bridge-lxc - Multi-channel communication bridge (Signal, Email, etc.)

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "staging" ]]; then
  echo "==> Creating bridge service in STAGING mode"
  source "${PCT_DIR}/stage-vars.env"
  PREFIX="${STAGE_PREFIX}"
  
  CT_BRIDGE="$CT_BRIDGE_STAGING"
  IP_BRIDGE="$IP_BRIDGE_STAGING"
else
  echo "==> Creating bridge service in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  PREFIX=""
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

# Create bridge container
create_ct "$CT_BRIDGE" "$IP_BRIDGE" "${PREFIX}bridge-lxc" unpriv || {
  echo "ERROR: Failed to create bridge container"
  exit 1
}

echo ""
echo "=========================================="
echo "Bridge service created successfully!"
echo "Mode: ${MODE}"
echo "Container:"
echo "  - ${PREFIX}bridge-lxc: $CT_BRIDGE @ $IP_BRIDGE"
echo "=========================================="
