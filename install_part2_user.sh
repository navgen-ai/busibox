#!/bin/bash
#
# Busibox Installation - Part 2 (User-level)
# Installs Rust, Ansible, and builds the Busibox CLI
#
# Usage: ./install_part2_user.sh
#

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

echo
echo "=============================================="
echo "  Busibox Installation - Part 2 (User)"
echo "=============================================="
echo

# Check if pip3 is available
if ! command -v pip3 &> /dev/null; then
    log_error "pip3 not found. Please run Part 1 first (sudo ./install_part1_sudo.sh)"
    exit 1
fi

# Install Rust
log_info "Checking Rust installation..."
if command -v rustc &> /dev/null; then
    log_success "Rust already installed: $(rustc --version)"
else
    log_info "Installing Rust toolchain..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
    log_success "Rust installed: $(rustc --version)"
fi

# Ensure Rust is in PATH
if [[ -f "$HOME/.cargo/env" ]]; then
    source "$HOME/.cargo/env"
fi

# Install Ansible
log_info "Checking Ansible installation..."
export PATH="$HOME/.local/bin:$PATH"

if command -v ansible &> /dev/null; then
    log_success "Ansible already installed: $(ansible --version | head -n1)"
else
    log_info "Installing Ansible..."
    # Use --break-system-packages for Ubuntu 24.04's PEP 668
    pip3 install --user --break-system-packages ansible ansible-core

    # Add to PATH if not already there
    if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    fi

    export PATH="$HOME/.local/bin:$PATH"
    log_success "Ansible installed: $(ansible --version | head -n1)"
fi

# Check Docker group membership
log_info "Checking Docker group membership..."
if groups $USER | grep -q docker; then
    log_success "User is in docker group"
else
    log_warning "User not in docker group"
    log_warning "Run: sudo usermod -aG docker $USER"
    log_warning "Then logout and login again"
fi

# Build Busibox CLI
log_info "Building Busibox CLI..."
cd "$SCRIPT_DIR/cli/busibox"

if cargo build --release; then
    log_success "Busibox CLI built successfully"
    log_info "CLI location: $SCRIPT_DIR/cli/busibox/target/release/busibox"
else
    log_error "Failed to build Busibox CLI"
    exit 1
fi

cd "$SCRIPT_DIR"

# Create .env file
log_info "Setting up environment file..."
if [[ ! -f "$SCRIPT_DIR/.env" ]] && [[ -f "$SCRIPT_DIR/env.local.example" ]]; then
    cp "$SCRIPT_DIR/env.local.example" "$SCRIPT_DIR/.env"
    log_success "Created .env from template"
else
    log_success ".env file already exists"
fi

# Check system resources
log_info "System resources:"
total_ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
total_ram_gb=$((total_ram_kb / 1024 / 1024))
echo "  RAM: ${total_ram_gb}GB"

available_gb=$(df -BG "$SCRIPT_DIR" | tail -1 | awk '{print $4}' | sed 's/G//')
echo "  Disk available: ${available_gb}GB"

if command -v nvidia-smi &> /dev/null; then
    echo "  GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)"
else
    echo "  GPU: Not detected"
fi

echo
echo "=============================================="
echo "  Installation Complete!"
echo "=============================================="
echo
log_success "Busibox CLI is ready to use"
echo
echo "Next steps:"
echo
echo "1. Load environment changes:"
echo "   source ~/.bashrc"
echo "   source ~/.cargo/env"
echo
echo "2. Launch the Busibox CLI:"
echo "   cd $SCRIPT_DIR/cli/busibox"
echo "   ./target/release/busibox"
echo
echo "3. Or deploy using Docker Compose:"
echo "   cd $SCRIPT_DIR"
echo "   docker compose up -d"
echo
echo "Documentation:"
echo "  $SCRIPT_DIR/INSTALL_UBUNTU_24.04.md"
echo
echo "=============================================="
