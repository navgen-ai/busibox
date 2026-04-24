# Busibox Installation Guide for Ubuntu 24.04

## Overview

This guide walks you through installing Busibox on Ubuntu 24.04 using Docker. Busibox is a self-hosted AI platform that provides document processing, semantic search, AI agents, and custom applications—all running on your own infrastructure.

## System Requirements

### Minimum Requirements
- **OS**: Ubuntu 24.04 LTS
- **CPU**: 4 cores
- **RAM**: 16 GB
- **Disk**: 100 GB available space
- **Network**: Internet connection for initial setup

### Recommended Requirements
- **CPU**: 8+ cores
- **RAM**: 32 GB+
- **Disk**: 500 GB+ SSD
- **GPU**: NVIDIA GPU with 8GB+ VRAM (for local LLM inference)

## Architecture

Busibox uses a microservices architecture with Docker containers:

```
┌─────────────────────────────────────────────────────────┐
│                       Browser                           │
└────────────────────────┬────────────────────────────────┘
                         │
                    ┌────▼────┐
                    │  nginx  │  reverse proxy + SSL
                    └────┬────┘
            ┌────────────┼────────────────┐
            ▼            ▼                ▼
      ┌──────────┐ ┌──────────┐    ┌───────────┐
      │  Portal  │ │  Agents  │    │ User Apps │
      └────┬─────┘ └────┬─────┘    └─────┬─────┘
           └─────────────┼────────────────┘
                         ▼
        ┌────────────────────────────────┐
        │         API Layer              │
        │  AuthZ · Data · Agent · Search │
        │  Docs · Deploy · Embedding     │
        └───────────────┬────────────────┘
                        ▼
        ┌────────────────────────────────┐
        │       Infrastructure           │
        │  PostgreSQL · Milvus · MinIO   │
        │  Redis · LiteLLM              │
        └────────────────────────────────┘
```

## Installation Methods

### Quick Start (Automated Script)

The fastest way to get started is using the automated installation script:

```bash
cd ~/maigent-code/busibox
chmod +x install_ubuntu_24.sh
./install_ubuntu_24.sh
```

The script will:
1. Install system dependencies
2. Install Rust toolchain
3. Install Docker and Docker Compose
4. Set up Python environment with Ansible
5. Build the Busibox CLI
6. Create initial configuration
7. Guide you through first-time setup

### Manual Installation

If you prefer to install manually, follow these steps:

#### Step 1: Install System Dependencies

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install build essentials
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
    python3-venv
```

#### Step 2: Install Rust

The Busibox CLI is written in Rust, so you need the Rust toolchain:

```bash
# Install rustup (Rust installer)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# Load Rust environment
source "$HOME/.cargo/env"

# Verify installation
rustc --version
cargo --version
```

#### Step 3: Install Docker

```bash
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

# Apply group changes (or logout and login again)
newgrp docker

# Verify installation
docker --version
docker compose version
```

#### Step 4: Install Ansible

Busibox uses Ansible for configuration management:

```bash
# Install Ansible and required Python packages
pip3 install --user ansible ansible-core

# Add pip binaries to PATH (add to ~/.bashrc for persistence)
export PATH="$HOME/.local/bin:$PATH"

# Verify installation
ansible --version
```

#### Step 5: Clone and Build Busibox CLI

```bash
# Navigate to the repository (already cloned at ~/maigent-code/busibox)
cd ~/maigent-code/busibox

# Build the Busibox CLI
cd cli/busibox
cargo build --release

# The binary will be at: target/release/busibox
```

#### Step 6: Set Up Docker Environment

```bash
# Return to repo root
cd ~/maigent-code/busibox

# Create environment file from example
cp env.local.example .env

# Edit .env file with your settings (optional for first run)
# nano .env
```

#### Step 7: Initialize Vault Password

The Busibox CLI manages secrets through an encrypted vault system:

```bash
cd ~/maigent-code/busibox/cli/busibox

# Run the CLI for first-time setup
./target/release/busibox
```

On first launch, the CLI will:
1. Prompt you to create a deployment profile
2. Ask you to choose deployment target (Docker local)
3. Create a master password for vault encryption
4. Generate random vault password (32-char, stored encrypted)
5. Save profile to `~/.busibox/profiles.json`
6. Save encrypted vault key to `~/.busibox/vault-keys/{profile}.enc`

## Configuration

### Docker Deployment (Recommended for Ubuntu 24.04)

For single-machine deployments, Docker is the simplest option:

1. **Launch the CLI**:
   ```bash
   cd ~/maigent-code/busibox/cli/busibox
   ./target/release/busibox
   ```

2. **Create Profile**:
   - Choose "Create new profile"
   - Select "Docker (local)"
   - Enter a profile name (e.g., "local-dev")
   - Create a master password (remember this!)

3. **Hardware Profiling**:
   - CLI will detect your CPU, RAM, and GPU
   - Recommends AI models based on your hardware
   - You can skip model download for now

4. **Deploy Services**:
   - Select "Install" from main menu
   - Choose "All services" for complete installation
   - Deployment takes 10-20 minutes

### Manual Deployment (Using Make)

If you prefer direct control, you can use Make commands:

```bash
# Navigate to Ansible directory
cd ~/maigent-code/busibox/provision/ansible

# Set vault password environment variable
export ANSIBLE_VAULT_PASSWORD="your-vault-password-here"

# Deploy all services
make docker

# Or deploy service groups individually:
make docker-infrastructure  # PostgreSQL, Redis, MinIO, Milvus
make docker-llm            # LiteLLM gateway
make docker-apis           # AuthZ, Data, Search, Agent APIs
make docker-frontend       # nginx, Portal, Agents apps
```

### Environment Configuration

Edit `.env` in the repository root to customize:

```bash
# Database
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=change_me_in_production
POSTGRES_DB=busibox

# MinIO (S3-compatible storage)
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=change_me_in_production

# AuthZ Service
JWT_SECRET_KEY=generate_a_secure_random_key_here
JWT_ALGORITHM=RS256

# LLM Configuration
LITELLM_BASE_URL=http://litellm:4000
# OPENAI_API_KEY=sk-...  # Optional: for OpenAI models

# Model Configuration
DEFAULT_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
DEFAULT_LLM_MODEL=gpt-4o-mini
```

## Verification

After deployment, verify all services are running:

### Using Docker Commands

```bash
# Check container status
docker compose ps

# Check service logs
docker compose logs -f authz    # AuthZ service
docker compose logs -f data-api # Data API
docker compose logs -f agent    # Agent API

# Health checks
curl http://localhost:8010/health/live  # AuthZ
curl http://localhost:8002/health       # Data API
curl http://localhost:8003/health       # Search API
curl http://localhost:8000/health       # Agent API
```

### Using the Busibox CLI

```bash
cd ~/maigent-code/busibox/cli/busibox
./target/release/busibox

# From the main menu:
# - Press 'm' for Manage screen
# - View service health indicators
# - Restart services if needed
# - View live logs
```

## Accessing Busibox

Once deployed, access the Busibox Portal:

```bash
# Default URL (Docker)
http://localhost:3000

# Or specific service URLs:
http://localhost:8010  # AuthZ service
http://localhost:8000  # Agent API
http://localhost:8002  # Data API
http://localhost:8003  # Search API
http://localhost:9001  # MinIO Console
```

### First Login

1. Navigate to `http://localhost:3000`
2. Create the first admin user account
3. The system will guide you through initial setup

## Common Operations

### Starting Services

```bash
# Using Docker Compose
cd ~/maigent-code/busibox
docker compose up -d

# Or using the CLI
cd cli/busibox
./target/release/busibox
# Select "Manage" → "Start" → "All services"
```

### Stopping Services

```bash
# Using Docker Compose
cd ~/maigent-code/busibox
docker compose down

# Or using the CLI
cd cli/busibox
./target/release/busibox
# Select "Manage" → "Stop" → "All services"
```

### Viewing Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f authz
docker compose logs -f agent
docker compose logs -f data-api
```

### Updating Busibox

```bash
# Pull latest code
cd ~/maigent-code/busibox
git pull origin main

# Rebuild CLI
cd cli/busibox
cargo build --release

# Redeploy services
cd ~/maigent-code/busibox
docker compose down
docker compose up -d --build
```

## Troubleshooting

### Docker Daemon Not Running

```bash
# Start Docker service
sudo systemctl start docker
sudo systemctl enable docker

# Check status
sudo systemctl status docker
```

### Permission Denied (Docker)

If you get "permission denied" errors:

```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Log out and log back in, or run:
newgrp docker
```

### Services Not Starting

```bash
# Check Docker logs
docker compose logs

# Check specific service
docker compose logs authz

# Restart a specific service
docker compose restart authz

# Full restart
docker compose down
docker compose up -d
```

### Database Connection Errors

```bash
# Check PostgreSQL is running
docker compose ps postgres

# Test database connection
docker compose exec postgres psql -U busibox_user -d busibox -c "SELECT 1"

# View database logs
docker compose logs postgres
```

### Port Conflicts

If ports are already in use:

```bash
# Check what's using a port
sudo lsof -i :8000
sudo lsof -i :5432

# Kill process or change ports in docker-compose.yml
```

### Low Memory Issues

If you have < 16GB RAM:

```bash
# Reduce Docker memory limits in docker-compose.yml
# Or deploy services individually:
cd provision/ansible

# Deploy only essential services
make docker-infrastructure  # Database, storage, vector DB
make docker-apis           # APIs only (skip LLM services)
```

## Next Steps

After installation:

1. **Configure AI Models**:
   - Use the CLI to download models
   - Configure LiteLLM for your preferred providers
   - Set up local vLLM for GPU inference (if available)

2. **Upload Documents**:
   - Use the Portal UI to upload PDFs, Word docs, etc.
   - Documents are automatically processed and indexed

3. **Create Agents**:
   - Configure agents with custom instructions
   - Set up RAG for document search
   - Deploy agents to Telegram, Discord, or web chat

4. **Build Custom Apps**:
   - Use the `busibox-template` to scaffold new apps
   - Apps inherit auth, data access, and AI capabilities
   - Deploy via the CLI or Portal UI

## Additional Resources

- **Documentation**: `docs/` directory in the repository
  - Administrators: `docs/administrators/`
  - Developers: `docs/developers/`
  - Users: `docs/users/`
- **Architecture**: `docs/developers/architecture/`
- **API Documentation**: Available at `http://localhost:8000/docs` after deployment
- **Testing Guide**: `TESTING.md`
- **Contributing**: `CLAUDE.md`

## Getting Help

- **GitHub Issues**: https://github.com/jazzmind/busibox/issues
- **Documentation**: Check the `docs/` directory
- **CLI Help**: Run `./target/release/busibox --help`

## Security Considerations

For production deployments:

1. **Change Default Passwords**: Update all passwords in `.env`
2. **Enable SSL/TLS**: Configure nginx with Let's Encrypt certificates
3. **Firewall**: Restrict access to internal services
4. **Backup**: Set up regular backups of PostgreSQL, Milvus, and MinIO
5. **Vault Security**: Store master password in a password manager
6. **Network Isolation**: Use Docker networks or VLANs

## License

Check the repository for license information.

---

**Version**: 1.0.0
**Last Updated**: 2026-03-26
**Platform**: Ubuntu 24.04 LTS
