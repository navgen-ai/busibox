#!/usr/bin/env bash
#
# install-menu.sh - Install menu with options for existing installs
#
# This script provides the same install options as the launcher menu:
# - Fresh install if no existing install
# - Continue/Full/Clean options if existing install detected
#
# Called by:
#   - make install (without SERVICE=)
#   - launcher.sh handle_install()
#
# Usage:
#   install-menu.sh                # Interactive menu (q = quit)
#   install-menu.sh --from-launcher # Called from launcher (b = back, clears screen)
#   install-menu.sh --direct       # Skip menu, go directly to install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/state.sh"

# Flags
DIRECT_MODE=false
FROM_LAUNCHER=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --direct)
            DIRECT_MODE=true
            shift
            ;;
        --from-launcher)
            FROM_LAUNCHER=true
            shift
            ;;
        *)
            # Pass through to install.sh
            break
            ;;
    esac
done

# =============================================================================
# DETECTION FUNCTIONS
# =============================================================================

# Check if Docker is available
check_docker_available() {
    command -v docker &>/dev/null && docker info &>/dev/null
}

# Check if Proxmox is available
check_proxmox_available() {
    command -v pct &>/dev/null
}

# Detect installation status for current environment
# Returns: not_installed, partial, installed
detect_installation_status() {
    local env backend
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    
    # No environment configured
    if [[ -z "$env" ]]; then
        echo "not_installed"
        return
    fi
    
    # Determine backend
    local env_upper
    env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
    backend=$(get_state "BACKEND_${env_upper}" 2>/dev/null || echo "")
    
    # Development always uses docker
    if [[ "$env" == "development" ]]; then
        backend="docker"
    fi
    
    if [[ -z "$backend" ]]; then
        echo "not_installed"
        return
    fi
    
    # Check if core services are running
    case "$backend" in
        docker)
            if ! check_docker_available 2>/dev/null; then
                echo "not_installed"
                return
            fi
            
            local prefix
            case "$env" in
                development) prefix="dev" ;;
                staging) prefix="staging" ;;
                production) prefix="prod" ;;
                demo) prefix="demo" ;;
                *) prefix="dev" ;;
            esac
            
            # Check for core services that must be running
            local core_services=("postgres" "authz-api" "core-apps")
            local running=0
            local total=${#core_services[@]}
            
            for service in "${core_services[@]}"; do
                if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${prefix}-${service}$"; then
                    ((running++))
                fi
            done
            
            # If all core services are running, system is installed
            if [[ $running -eq $total ]]; then
                echo "installed"
            elif [[ $running -gt 0 ]]; then
                echo "partial"
            else
                echo "not_installed"
            fi
            ;;
        proxmox)
            if ! check_proxmox_available 2>/dev/null; then
                echo "not_installed"
                return
            fi
            
            local base_ctid
            case "$env" in
                production) base_ctid=200 ;;
                staging) base_ctid=300 ;;
                *) base_ctid=300 ;;
            esac
            
            # Check if proxy container is running
            local status
            status=$(pct status "$base_ctid" 2>/dev/null | awk '{print $2}')
            if [[ "$status" == "running" ]]; then
                echo "installed"
            else
                echo "not_installed"
            fi
            ;;
        *)
            echo "not_installed"
            ;;
    esac
}

# Get backend for current environment
get_current_backend() {
    local env="$1"
    local env_upper
    env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
    
    # Development always uses docker
    if [[ "$env" == "development" ]]; then
        echo "docker"
        return
    fi
    
    get_state "BACKEND_${env_upper}" 2>/dev/null || echo ""
}

# =============================================================================
# UNINSTALL FUNCTION
# =============================================================================

perform_uninstall() {
    local env="$1"
    local backend="$2"
    
    info "Uninstalling Busibox..."
    
    if [[ "$backend" == "docker" ]]; then
        local prefix
        case "$env" in
            development) prefix="dev" ;;
            staging) prefix="staging" ;;
            production) prefix="prod" ;;
            demo) prefix="demo" ;;
            *) prefix="dev" ;;
        esac
        
        info "Stopping and removing Docker containers (prefix: ${prefix})..."
        
        # Stop and remove containers
        docker ps -a --filter "name=${prefix}-" --format '{{.Names}}' | while read -r container; do
            info "Removing container: $container"
            docker stop "$container" 2>/dev/null || true
            docker rm "$container" 2>/dev/null || true
        done
        
        # Remove volumes
        info "Removing Docker volumes..."
        docker volume ls --filter "name=${prefix}" --format '{{.Name}}' | while read -r volume; do
            info "Removing volume: $volume"
            docker volume rm "$volume" 2>/dev/null || true
        done
        
        # Remove network
        info "Removing Docker network..."
        docker network rm "${prefix}-busibox" 2>/dev/null || true
        
        # Remove env file
        local env_file="${REPO_ROOT}/.env.${prefix}"
        if [[ -f "$env_file" ]]; then
            info "Removing env file: $env_file"
            rm -f "$env_file"
        fi
        
        success "Docker uninstall complete"
        
    elif [[ "$backend" == "proxmox" ]]; then
        local base_ctid
        case "$env" in
            production) base_ctid=200 ;;
            staging) base_ctid=300 ;;
            *)
                error "Unknown environment for Proxmox: $env"
                return 1
                ;;
        esac
        
        warn "Proxmox uninstall will stop and destroy containers ${base_ctid}-$((base_ctid+19))"
        read -p "Are you sure? Type 'YES' to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            echo "Cancelled."
            return 1
        fi
        
        for ctid in $(seq "$base_ctid" $((base_ctid+19))); do
            if pct status "$ctid" &>/dev/null; then
                info "Stopping and destroying container $ctid..."
                pct stop "$ctid" 2>/dev/null || true
                pct destroy "$ctid" 2>/dev/null || true
            fi
        done
        
        success "Proxmox uninstall complete"
    else
        error "Unknown backend: $backend"
        return 1
    fi
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    # If direct mode, skip menu and go to install.sh
    if [[ "$DIRECT_MODE" == true ]]; then
        exec bash "${SCRIPT_DIR}/install.sh" "$@"
    fi
    
    local install_status
    install_status=$(detect_installation_status)
    
    # Get current environment info
    local env backend
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    backend=$(get_current_backend "$env")
    
    if [[ "$install_status" == "installed" || "$install_status" == "partial" ]]; then
        # Show install options submenu
        if [[ "$FROM_LAUNCHER" == true ]]; then
            clear
        fi
        echo ""
        box_start 70 double "$CYAN"
        box_header "INSTALL OPTIONS"
        box_empty
        if [[ -n "$env" ]]; then
            box_line "  ${CYAN}Environment:${NC} $env ($backend)"
            box_empty
            box_separator
            box_empty
        fi
        box_line "  ${BOLD}1)${NC} Continue Install - pick up from where we last failed"
        box_line "  ${BOLD}2)${NC} Full Install - redeploy all services, keep config & data"
        box_line "  ${BOLD}3)${NC} Clean Install - clear everything and start fresh"
        box_empty
        if [[ "$FROM_LAUNCHER" == true ]]; then
            box_line "  ${DIM}b = back${NC}"
        else
            box_line "  ${DIM}q = quit${NC}"
        fi
        box_empty
        box_footer
        echo ""
        
        read -n 1 -s -r -p "Select option: " choice
        echo ""
        
        case "$choice" in
            1)
                # Continue - resume from current install phase
                if [[ -n "$env" ]]; then
                    exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" "$@"
                else
                    exec bash "${SCRIPT_DIR}/install.sh" "$@"
                fi
                ;;
            2)
                # Full Install - reset install phase but keep config
                echo ""
                printf "${YELLOW}This will redeploy all services but preserve your configuration and data.${NC}\n"
                read -p "Continue? [y/N]: " confirm
                if [[ "${confirm,,}" == "y" ]]; then
                    # Reset install phase to force full redeploy
                    set_state "INSTALL_PHASE" "wizard_complete"
                    # Clear container creation flags to force service redeploy
                    if [[ "$backend" == "proxmox" ]]; then
                        # Keep LXC containers but force service redeploy
                        set_state "LXC_CONTAINERS_CREATED_${env^^}" "true"
                    fi
                    if [[ -n "$env" ]]; then
                        exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" --full-install "$@"
                    else
                        exec bash "${SCRIPT_DIR}/install.sh" --full-install "$@"
                    fi
                else
                    echo "Cancelled."
                    exit 0
                fi
                ;;
            3)
                # Clean Install - full uninstall then fresh install
                echo ""
                printf "${RED}Warning: This will remove ALL containers, data, and configuration.${NC}\n"
                read -p "Type 'CLEAN' to confirm: " confirm
                if [[ "$confirm" == "CLEAN" ]]; then
                    perform_uninstall "$env" "$backend"
                    # Clear state file for this environment
                    local state_prefix
                    case "$env" in
                        development) state_prefix="dev" ;;
                        staging) state_prefix="staging" ;;
                        production) state_prefix="prod" ;;
                        demo) state_prefix="demo" ;;
                        *) state_prefix="dev" ;;
                    esac
                    local state_file="${REPO_ROOT}/.busibox-state-${state_prefix}"
                    if [[ -f "$state_file" ]]; then
                        info "Removing state file: $state_file"
                        rm -f "$state_file"
                    fi
                    echo ""
                    info "Starting fresh install..."
                    sleep 1
                    exec bash "${SCRIPT_DIR}/install.sh" "$@"
                else
                    echo "Cancelled."
                    exit 0
                fi
                ;;
            b|B)
                if [[ "$FROM_LAUNCHER" == true ]]; then
                    # Return to launcher menu
                    exit 0
                else
                    echo "Exiting."
                    exit 0
                fi
                ;;
            q|Q)
                echo "Exiting."
                exit 0
                ;;
            *)
                echo "Invalid option. Exiting."
                exit 1
                ;;
        esac
    else
        # Fresh install - go directly to install wizard
        if [[ -z "$env" ]]; then
            exec bash "${SCRIPT_DIR}/install.sh" "$@"
        else
            exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" "$@"
        fi
    fi
}

main "$@"
