#!/usr/bin/env bash
#
# Migrate TEST- Container Names to STAGE-
#
# Description:
#   Renames all existing TEST-* LXC container hostnames to STAGE-* prefix.
#   This is a one-time migration script for the naming convention change.
#
# Execution Context: Proxmox VE Host
# Dependencies: pct
#
# Usage:
#   bash provision/pct/migrate-test-to-stage.sh [--dry-run]
#
# Arguments:
#   --dry-run  Show what would be changed without making changes
#
# Notes:
#   - Containers must be stopped to rename (script will stop/start)
#   - After running, update ~/.ssh/known_hosts if needed
#   - Run Ansible to update internal DNS on all containers

set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "==> DRY RUN MODE - No changes will be made"
  echo ""
fi

echo "=========================================="
echo "Busibox Container Name Migration"
echo "TEST-* -> STAGE-*"
echo "=========================================="
echo ""

# Find all TEST-* containers
CONTAINERS=$(pct list 2>/dev/null | grep -E "TEST-" | awk '{print $1}') || true

if [[ -z "$CONTAINERS" ]]; then
  echo "No TEST-* containers found. Migration may have already been completed."
  exit 0
fi

echo "Found containers to migrate:"
for ctid in $CONTAINERS; do
  old_name=$(pct config "$ctid" 2>/dev/null | grep "^hostname:" | cut -d: -f2 | tr -d ' ' || echo "unknown")
  echo "  - $ctid: $old_name"
done
echo ""

if [[ "$DRY_RUN" == "false" ]]; then
  read -p "Proceed with migration? (y/N) " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Migration cancelled."
    exit 0
  fi
  echo ""
fi

# Track results
MIGRATED=()
FAILED=()

for ctid in $CONTAINERS; do
  old_name=$(pct config "$ctid" 2>/dev/null | grep "^hostname:" | cut -d: -f2 | tr -d ' ' || echo "")
  
  if [[ -z "$old_name" ]]; then
    echo "  SKIP: Could not get hostname for container $ctid"
    continue
  fi
  
  # Replace TEST- with STAGE-
  new_name="${old_name/TEST-/STAGE-}"
  
  if [[ "$old_name" == "$new_name" ]]; then
    echo "  SKIP: Container $ctid ($old_name) does not have TEST- prefix"
    continue
  fi
  
  echo "  Migrating container $ctid: $old_name -> $new_name"
  
  if [[ "$DRY_RUN" == "true" ]]; then
    MIGRATED+=("$ctid:$old_name->$new_name")
    continue
  fi
  
  # Check if container is running
  was_running=false
  if pct status "$ctid" 2>/dev/null | grep -q "running"; then
    was_running=true
    echo "    Stopping container..."
    if ! pct stop "$ctid" 2>/dev/null; then
      echo "    ERROR: Failed to stop container"
      FAILED+=("$ctid:$old_name")
      continue
    fi
    sleep 2
  fi
  
  # Rename container
  echo "    Setting new hostname..."
  if ! pct set "$ctid" --hostname "$new_name" 2>/dev/null; then
    echo "    ERROR: Failed to set hostname"
    FAILED+=("$ctid:$old_name")
    # Try to restart if it was running
    if [[ "$was_running" == "true" ]]; then
      pct start "$ctid" 2>/dev/null || true
    fi
    continue
  fi
  
  # Restart if it was running
  if [[ "$was_running" == "true" ]]; then
    echo "    Starting container..."
    if ! pct start "$ctid" 2>/dev/null; then
      echo "    WARNING: Failed to start container (may need manual intervention)"
    fi
    sleep 1
  fi
  
  MIGRATED+=("$ctid:$old_name->$new_name")
  echo "    Done"
done

echo ""
echo "=========================================="
echo "Migration Summary"
echo "=========================================="
echo ""

if [[ ${#MIGRATED[@]} -gt 0 ]]; then
  echo "Successfully migrated (${#MIGRATED[@]}):"
  for item in "${MIGRATED[@]}"; do
    echo "  - $item"
  done
  echo ""
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "Failed (${#FAILED[@]}):"
  for item in "${FAILED[@]}"; do
    echo "  - $item"
  done
  echo ""
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "DRY RUN completed. Run without --dry-run to apply changes."
else
  echo "Next steps:"
  echo "  1. Update ~/.ssh/known_hosts if SSH host keys changed"
  echo "  2. Run Ansible to update internal DNS:"
  echo "     cd provision/ansible && make internal-dns INV=inventory/staging"
  echo "  3. Verify container connectivity:"
  echo "     ansible -i inventory/staging/hosts.yml all -m ping"
fi

echo "=========================================="
