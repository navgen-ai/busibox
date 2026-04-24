#!/bin/bash
#
# Busibox Installation Script for Ubuntu 24.04
#
# This script automates the installation of Busibox on Ubuntu 24.04 systems.
# It installs all prerequisites, builds the CLI, and guides you through initial setup.
#
# Usage:
#   chmod +x install_ubuntu_24.sh
#   ./install_ubuntu_24.sh
#
# Requirements:
#   - Ubuntu 24.04 LTS
#   - sudo privileges
#   - Internet connection
#

set -e  # Exit on error
set -u  # Exit on undefined variable

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Log functions
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

# Check if running as root
check_not_root() {
    if [[ $EUID -eq 0 ]]; then
        log_error "This script should NOT be run as root"
        log_error "Please run as a regular user with sudo privileges"
        exit 1
    fi
}

# Check Ubuntu version
check_ubuntu_version() {
    log_info "Checking Ubuntu version..."

    if [[ ! -f /etc/os-release ]]; then
        log_error "Cannot determine OS version"
        exit 1
    fi

    source /etc/os-release

    if [[ "$ID" != "ubuntu" ]]; then
        log_error "This script is designed for Ubuntu. Detected: $ID"
        exit 1
    fi

    if [[ "$VERSION_ID" != "24.04" ]]; then
        log_warning "This script is designed for Ubuntu 24.04. Detected: $VERSION_ID"
        read -p "Continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    log_success "Ubuntu $VERSION_ID detected"
}

# Check sudo privileges
check_sudo() {
    log_info "Checking sudo privileges..."

    # Try passwordless sudo first
    if sudo -n true 2>/dev/null; then
        log_success "Passwordless sudo confirmed"
        return 0
    fi

    # If not passwordless, warn but continue
    log_warning "Passwordless sudo is not configured"
    log_warning "You will be prompted for password during package installation"

    return 0
}

# Update system
update_system() {
    log_info "Updating system packages..."
    sudo apt-get update
    log_success "System packages updated"
}

# Install system dependencies
install_system_deps() {
    log_info "Installing system dependencies..."

    sudo apt-get install -y \
        build-essential \
        pkg-config \
        libssl-dev \
        git \
        curl \
        wget \
        ca-certificates \
        gnupg \
        lsb-release \
        python3 \
        python3-pip \
        python3-venv \
        software-properties-common

    log_success "System dependencies installed"
}

# Install Rust
install_rust() {
    log_info "Checking Rust installation..."

    if command -v rustc &> /dev/null; then
        local rust_version=$(rustc --version)
        log_success "Rust already installed: $rust_version"
        return 0
    fi

    log_info "Installing Rust toolchain..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

    # Source Rust environment
    source "$HOME/.cargo/env"

    # Verify installation
    if command -v rustc &> /dev/null; then
        local rust_version=$(rustc --version)
        log_success "Rust installed successfully: $rust_version"
    else
        log_error "Rust installation failed"
        exit 1
    fi
}

# Install Docker
install_docker() {
    log_info "Checking Docker installation..."

    if command -v docker &> /dev/null; then
        local docker_version=$(docker --version)
        log_success "Docker already installed: $docker_version"

        # Check if user is in docker group
        if groups $USER | grep -q docker; then
            log_success "User already in docker group"
        else
            log_warning "User not in docker group, adding..."
            sudo usermod -aG docker $USER
            log_success "User added to docker group (logout/login to apply)"
        fi

        return 0
    fi

    log_info "Installing Docker..."

    # Add Docker's official GPG key
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    # Set up Docker repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Install Docker Engine
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Add current user to docker group
    sudo usermod -aG docker $USER

    # Start and enable Docker
    sudo systemctl start docker
    sudo systemctl enable docker

    # Verify installation
    if command -v docker &> /dev/null; then
        local docker_version=$(docker --version)
        log_success "Docker installed successfully: $docker_version"
        log_warning "You may need to logout and login for docker group changes to take effect"
    else
        log_error "Docker installation failed"
        exit 1
    fi
}

# Install Ansible
install_ansible() {
    log_info "Checking Ansible installation..."

    if command -v ansible &> /dev/null; then
        local ansible_version=$(ansible --version | head -n1)
        log_success "Ansible already installed: $ansible_version"
        return 0
    fi

    log_info "Installing Ansible..."
    pip3 install --user ansible ansible-core

    # Add pip binaries to PATH
    export PATH="$HOME/.local/bin:$PATH"

    # Add to bashrc if not already present
    if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
        log_info "Added pip binaries to PATH in ~/.bashrc"
    fi

    # Verify installation
    if command -v ansible &> /dev/null; then
        local ansible_version=$(ansible --version | head -n1)
        log_success "Ansible installed successfully: $ansible_version"
    else
        log_error "Ansible installation failed"
        log_error "Try running: source ~/.bashrc"
        exit 1
    fi
}

# Install Ansible dependencies
install_ansible_deps() {
    log_info "Installing Ansible dependencies..."

    cd "$SCRIPT_DIR/provision/ansible"

    if [[ -f "requirements.txt" ]]; then
        pip3 install --user -r requirements.txt
        log_success "Ansible Python dependencies installed"
    fi

    if [[ -f "requirements.yml" ]]; then
        ansible-galaxy install -r requirements.yml
        log_success "Ansible Galaxy collections installed"
    fi

    cd "$SCRIPT_DIR"
}

# Build Busibox CLI
build_cli() {
    log_info "Building Busibox CLI..."

    cd "$SCRIPT_DIR/cli/busibox"

    # Ensure Rust is in PATH
    if [[ -f "$HOME/.cargo/env" ]]; then
        source "$HOME/.cargo/env"
    fi

    # Build in release mode
    cargo build --release

    # Verify build
    if [[ -f "target/release/busibox" ]]; then
        log_success "Busibox CLI built successfully"
        log_info "CLI location: $SCRIPT_DIR/cli/busibox/target/release/busibox"
    else
        log_error "Failed to build Busibox CLI"
        exit 1
    fi

    cd "$SCRIPT_DIR"
}

# Create environment file
setup_env_file() {
    log_info "Setting up environment file..."

    if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
        if [[ -f "$SCRIPT_DIR/env.local.example" ]]; then
            cp "$SCRIPT_DIR/env.local.example" "$SCRIPT_DIR/.env"
            log_success "Created .env from env.local.example"
            log_warning "Review and edit .env file with your configuration"
        else
            log_warning "env.local.example not found, skipping .env creation"
        fi
    else
        log_success ".env file already exists"
    fi
}

# Check system resources
check_resources() {
    log_info "Checking system resources..."

    # Check RAM
    local total_ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local total_ram_gb=$((total_ram_kb / 1024 / 1024))

    if [[ $total_ram_gb -lt 16 ]]; then
        log_warning "System has ${total_ram_gb}GB RAM. Recommended: 16GB+"
        log_warning "You may experience performance issues or need to deploy services individually"
    else
        log_success "RAM: ${total_ram_gb}GB (sufficient)"
    fi

    # Check disk space
    local available_gb=$(df -BG "$SCRIPT_DIR" | tail -1 | awk '{print $4}' | sed 's/G//')

    if [[ $available_gb -lt 100 ]]; then
        log_warning "Available disk space: ${available_gb}GB. Recommended: 100GB+"
    else
        log_success "Disk space: ${available_gb}GB available (sufficient)"
    fi

    # Check for GPU
    if command -v nvidia-smi &> /dev/null; then
        log_success "NVIDIA GPU detected"
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | while read line; do
            log_info "  $line"
        done
    else
        log_info "No NVIDIA GPU detected (optional, for local LLM inference)"
    fi
}

# Print next steps
print_next_steps() {
    echo
    echo "=============================================="
    echo "  Busibox Installation Complete!"
    echo "=============================================="
    echo
    log_success "All prerequisites installed successfully"
    echo
    echo "Next steps:"
    echo
    echo "1. Load environment changes:"
    echo "   source ~/.bashrc"
    echo "   source ~/.cargo/env"
    echo
    echo "2. (IMPORTANT) If Docker was just installed, logout and login"
    echo "   to apply docker group membership"
    echo
    echo "3. Launch the Busibox CLI:"
    echo "   cd $SCRIPT_DIR/cli/busibox"
    echo "   ./target/release/busibox"
    echo
    echo "4. Or use Make commands for deployment:"
    echo "   cd $SCRIPT_DIR/provision/ansible"
    echo "   export ANSIBLE_VAULT_PASSWORD='your-vault-password'"
    echo "   make docker"
    echo
    echo "Documentation:"
    echo "  - Full guide: $SCRIPT_DIR/INSTALL_UBUNTU_24.04.md"
    echo "  - Administrators: $SCRIPT_DIR/docs/administrators/"
    echo "  - Developers: $SCRIPT_DIR/docs/developers/"
    echo
    echo "For help:"
    echo "  - Run: ./target/release/busibox --help"
    echo "  - Read: $SCRIPT_DIR/docs/administrators/01-quickstart.md"
    echo
    echo "=============================================="
}

# Main installation flow
main() {
    echo
    echo "=============================================="
    echo "  Busibox Installation Script"
    echo "  Ubuntu 24.04"
    echo "=============================================="
    echo

    # Pre-flight checks
    check_not_root
    check_ubuntu_version
    check_sudo

    # System setup
    update_system
    install_system_deps

    # Install dependencies
    install_rust
    install_docker
    install_ansible
    install_ansible_deps

    # Build Busibox
    build_cli
    setup_env_file

    # System checks
    check_resources

    # Done
    print_next_steps
}

# Run main function
main "$@"
