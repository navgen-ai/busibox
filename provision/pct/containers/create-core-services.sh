#!/usr/bin/env bash
#
# Create Core Services LXC Containers
#
# Description:
#   Creates core infrastructure containers: proxy, core-apps, user-apps, agent, and authz.
#   These are the main application containers for the Busibox platform.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct, provision/pct/lib/functions.sh
#
# Usage:
#   bash provision/pct/containers/create-core-services.sh [staging|production]
#
# Containers Created:
#   - proxy-lxc      - nginx reverse proxy (main entry point)
#   - core-apps-lxc  - Core Next.js applications (busibox-portal, busibox-agents)
#   - user-apps-lxc  - External/user-deployed applications
#   - custom-services-lxc - Custom Docker Compose service stacks
#   - agent-lxc      - Agent API server
#   - authz-lxc      - Authorization service (RLS token issuer)

set -euo pipefail

# Determine mode from argument
MODE="${1:-production}"

# Get script directory and source dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"

# Source configuration
if [[ "$MODE" == "staging" ]]; then
  echo "==> Creating core services in STAGING mode"
  source "${PCT_DIR}/stage-vars.env"
  PREFIX="${STAGE_PREFIX}"
  
  CT_PROXY="$CT_PROXY_STAGING"
  CT_CORE_APPS="$CT_CORE_APPS_STAGING"
  CT_USER_APPS="$CT_USER_APPS_STAGING"
  CT_CUSTOM_SERVICES="$CT_CUSTOM_SERVICES_STAGING"
  CT_AGENT="$CT_AGENT_STAGING"
  CT_AUTHZ="$CT_AUTHZ_STAGING"
  
  IP_PROXY="$IP_PROXY_STAGING"
  IP_CORE_APPS="$IP_CORE_APPS_STAGING"
  IP_USER_APPS="$IP_USER_APPS_STAGING"
  IP_CUSTOM_SERVICES="$IP_CUSTOM_SERVICES_STAGING"
  IP_AGENT="$IP_AGENT_STAGING"
  IP_AUTHZ="$IP_AUTHZ_STAGING"
else
  echo "==> Creating core services in PRODUCTION mode"
  source "${PCT_DIR}/vars.env"
  PREFIX=""
fi

# Source common functions
source "${PCT_DIR}/lib/functions.sh"

# Validate environment
validate_env || exit 1

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

# Create proxy container
create_and_track "$CT_PROXY" "$IP_PROXY" "${PREFIX}proxy-lxc" unpriv

# Create core-apps container (busibox-portal, busibox-agents)
create_and_track "$CT_CORE_APPS" "$IP_CORE_APPS" "${PREFIX}core-apps-lxc" unpriv

# Create user-apps container (external/user-deployed apps)
create_and_track "$CT_USER_APPS" "$IP_USER_APPS" "${PREFIX}user-apps-lxc" unpriv

# Create custom-services container (custom Docker Compose stacks)
create_and_track "$CT_CUSTOM_SERVICES" "$IP_CUSTOM_SERVICES" "${PREFIX}custom-services-lxc" unpriv

# Configure Docker sysctls support for custom-services (runs Docker-in-LXC)
CONFIG_FILE="/etc/pve/lxc/${CT_CUSTOM_SERVICES}.conf"
if ! grep -q "lxc.apparmor.profile" "$CONFIG_FILE"; then
  echo "    Configuring Docker/AppArmor support for custom-services..."
  pct stop "$CT_CUSTOM_SERVICES" 2>/dev/null || true
  sleep 2
  cat >> "$CONFIG_FILE" << 'EOF'
# Docker-in-LXC support - newer containerd requires unconfined AppArmor
# to avoid "sysctl net.ipv4.ip_unprivileged_port_start permission denied"
lxc.apparmor.profile: unconfined
lxc.mount.entry: /dev/null sys/module/apparmor/parameters/enabled none bind 0 0
EOF
  pct start "$CT_CUSTOM_SERVICES"
  sleep 3
  echo "    Docker/AppArmor support configured"
fi

# Create agent container
create_and_track "$CT_AGENT" "$IP_AGENT" "${PREFIX}agent-lxc" unpriv

# Create authz container
create_and_track "$CT_AUTHZ" "$IP_AUTHZ" "${PREFIX}authz-lxc" unpriv

echo ""
echo "=========================================="
echo "Core services created successfully!"
echo "Mode: ${MODE}"
echo "Containers:"
echo "  - ${PREFIX}proxy-lxc:      $CT_PROXY @ $IP_PROXY"
echo "  - ${PREFIX}core-apps-lxc:  $CT_CORE_APPS @ $IP_CORE_APPS"
echo "  - ${PREFIX}user-apps-lxc:  $CT_USER_APPS @ $IP_USER_APPS"
echo "  - ${PREFIX}custom-services-lxc: $CT_CUSTOM_SERVICES @ $IP_CUSTOM_SERVICES"
echo "  - ${PREFIX}agent-lxc:      $CT_AGENT @ $IP_AGENT"
echo "  - ${PREFIX}authz-lxc:      $CT_AUTHZ @ $IP_AUTHZ"
echo "=========================================="

