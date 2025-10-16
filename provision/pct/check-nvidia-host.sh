#!/usr/bin/env bash
#
# Check and fix NVIDIA driver setup on Proxmox host
#
# This script:
# 1. Checks if NVIDIA drivers are installed on the host
# 2. Verifies the driver version
# 3. Optionally installs/updates to cuda-drivers meta-package
#
# Usage:
#   bash check-nvidia-host.sh [--fix]
#

set -euo pipefail

# --- Configuration ---
FIX_MODE=false
if [[ "${1:-}" == "--fix" ]]; then
  FIX_MODE=true
fi

# --- Colors for output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Logging functions ---
log_info() {
  echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*"
}

# --- Main checks ---
main() {
  log_info "Checking NVIDIA driver setup on Proxmox host..."
  echo ""
  
  # Check if nvidia-smi is available
  if ! command -v nvidia-smi &>/dev/null; then
    log_error "nvidia-smi not found on host!"
    if [[ "$FIX_MODE" == "true" ]]; then
      install_nvidia_drivers
    else
      log_info "Run with --fix to install NVIDIA drivers"
      exit 1
    fi
  else
    log_success "nvidia-smi found"
  fi
  
  # Get driver version
  if nvidia-smi &>/dev/null; then
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
    log_success "NVIDIA Driver Version: $DRIVER_VERSION"
    
    # Check if driver is compatible with PyTorch (550.x recommended)
    MAJOR_VERSION=$(echo "$DRIVER_VERSION" | cut -d. -f1)
    if [[ "$MAJOR_VERSION" -gt 550 ]]; then
      log_warning "Driver version $DRIVER_VERSION may be incompatible with PyTorch!"
      log_warning "PyTorch requires CUDA 12.4 which is best supported by driver 550.x"
      log_warning "See: https://discuss.pytorch.org/t/no-more-cuda-available-after-installing-last-nvidia-drivers/203376"
      if [[ "$FIX_MODE" == "true" ]]; then
        log_info "Downgrading to driver 550..."
        purge_and_reinstall
      else
        log_info "Run with --fix to downgrade to driver 550"
      fi
    elif [[ "$MAJOR_VERSION" -lt 550 ]]; then
      log_warning "Driver version $DRIVER_VERSION is older than recommended 550.x"
      if [[ "$FIX_MODE" == "true" ]]; then
        log_info "Upgrading to driver 550..."
        purge_and_reinstall
      else
        log_info "Run with --fix to upgrade to driver 550"
      fi
    else
      log_success "Driver version is compatible with PyTorch CUDA 12.4"
    fi
  else
    log_error "nvidia-smi failed to run"
    if [[ "$FIX_MODE" == "true" ]]; then
      log_info "Attempting to fix..."
      purge_and_reinstall
    else
      log_info "Run with --fix to reinstall NVIDIA drivers"
      exit 1
    fi
  fi
  
  # Check kernel module version
  if [[ -f /proc/driver/nvidia/version ]]; then
    log_info "Kernel module info:"
    cat /proc/driver/nvidia/version | head -1
    echo ""
  else
    log_warning "NVIDIA kernel module not loaded"
  fi
  
  # Check installed packages
  log_info "Checking installed NVIDIA packages..."
  if dpkg -l | grep -E '^ii.*nvidia' | grep -v lib | head -10; then
    echo ""
  else
    log_warning "No NVIDIA driver packages found"
  fi
  
  # Check for cuda-drivers meta-package
  if dpkg -l | grep -q "^ii.*cuda-drivers"; then
    log_success "cuda-drivers meta-package is installed"
  else
    log_warning "cuda-drivers meta-package NOT installed"
    if [[ "$FIX_MODE" == "true" ]]; then
      install_cuda_drivers_metapackage
    else
      log_info "Run with --fix to install cuda-drivers meta-package"
    fi
  fi
  
  # List GPUs
  log_info "Available GPUs:"
  nvidia-smi -L
  echo ""
  
  log_success "NVIDIA host check complete!"
}

install_nvidia_drivers() {
  log_info "Installing NVIDIA driver 550 (CUDA 12.4 compatible) on host..."
  
  # First, clean uninstall any existing drivers
  log_info "Checking for existing NVIDIA drivers..."
  if dpkg -l | grep -q "^ii.*nvidia-driver"; then
    log_warning "Existing NVIDIA drivers detected - performing clean removal..."
    
    # Stop NVIDIA services
    systemctl stop nvidia-persistenced 2>/dev/null || true
    
    # Unload kernel modules
    log_info "Unloading NVIDIA kernel modules..."
    rmmod nvidia_uvm 2>/dev/null || true
    rmmod nvidia_drm 2>/dev/null || true
    rmmod nvidia_modeset 2>/dev/null || true
    rmmod nvidia 2>/dev/null || true
    
    # Purge all NVIDIA packages
    log_info "Purging old NVIDIA packages..."
    apt-get purge -y 'nvidia-driver-*' 'nvidia-kernel-*' 'nvidia-dkms-*' || true
    apt-get purge -y 'cuda-drivers-*' 'cuda-toolkit-*' 'cuda-runtime-*' || true
    apt-get purge -y 'libnvidia-*' 'libcuda*' 'nvidia-utils-*' || true
    apt-get autoremove -y
    apt-get autoclean
    
    log_success "Old drivers removed"
  fi
  
  # Add NVIDIA repository if not present
  if [[ ! -f /etc/apt/sources.list.d/nvidia-cuda.list ]]; then
    log_info "Adding NVIDIA CUDA repository..."
    
    # Remove old keyring files if present
    rm -f /usr/share/keyrings/nvidia-cuda-keyring.* 2>/dev/null || true
    
    wget -q https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/3bf863cc.pub -O /tmp/nvidia-cuda-keyring.asc
    
    if [[ ! -f /tmp/nvidia-cuda-keyring.asc ]]; then
      log_error "Failed to download NVIDIA keyring"
      exit 1
    fi
    
    gpg --dearmor < /tmp/nvidia-cuda-keyring.asc > /usr/share/keyrings/nvidia-cuda-keyring.gpg
    chmod 644 /usr/share/keyrings/nvidia-cuda-keyring.gpg
    
    echo "deb [signed-by=/usr/share/keyrings/nvidia-cuda-keyring.gpg] https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/ /" > /etc/apt/sources.list.d/nvidia-cuda.list
    
    rm -f /tmp/nvidia-cuda-keyring.asc
  fi
  
  log_info "Updating package lists..."
  apt-get update
  
  # Install specific driver version 550 (compatible with CUDA 12.4 and PyTorch)
  log_info "Installing NVIDIA driver 550..."
  apt-get install -y nvidia-driver-550 cuda-drivers-550
  
  log_warning "=========================================="
  log_warning "NVIDIA driver 550 installed!"
  log_warning "HOST REBOOT REQUIRED!"
  log_warning "=========================================="
  log_info "Run: reboot"
  log_info "After reboot, verify with: nvidia-smi"
  log_info "Should show driver version 550.x and CUDA 12.4/12.5"
  log_info ""
  log_info "Reference: https://discuss.pytorch.org/t/no-more-cuda-available-after-installing-last-nvidia-drivers/203376"
}

install_cuda_drivers_metapackage() {
  log_info "Installing cuda-drivers-550 package..."
  
  # Ensure NVIDIA repo is configured
  if [[ ! -f /etc/apt/sources.list.d/nvidia-cuda.list ]]; then
    install_nvidia_drivers
    return
  fi
  
  apt-get update
  apt-get install -y cuda-drivers-550
  
  log_success "cuda-drivers-550 package installed"
}

purge_and_reinstall() {
  log_warning "Purging and reinstalling NVIDIA drivers..."
  
  # The install_nvidia_drivers function now handles clean removal
  install_nvidia_drivers
}

# Run main function
main

