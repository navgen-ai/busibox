#!/bin/bash
#
# Busibox Installation - Part 1 (Requires sudo)
# Run this script manually to install system packages
#
# Usage: sudo ./install_part1_sudo.sh
#

set -e

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "=============================================="
echo "  Busibox Installation - Part 1 (System)"
echo "=============================================="
echo

echo -e "${BLUE}[INFO]${NC} Installing system dependencies..."

# Update package list
apt-get update

# Install system dependencies
apt-get install -y \
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

echo -e "${GREEN}[SUCCESS]${NC} System dependencies installed"
echo
echo "Next step: Run the user-level installation script"
echo "  ./install_part2_user.sh"
echo
