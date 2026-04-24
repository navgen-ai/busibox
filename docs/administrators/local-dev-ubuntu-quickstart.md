---
title: "Local Dev Quickstart (Ubuntu 24.04)"
category: "administrator"
order: 12
description: "Concise Docker-based dev setup for Ubuntu 24.04, focused on the vault/env hurdles that trip up a first-time install"
published: true
---

# Local Dev Quickstart (Ubuntu 24.04)

A focused guide for getting Busibox running locally on Ubuntu 24.04 via Docker. This complements `02-install.md` and `01-quickstart.md` by calling out the vault and env-file hurdles that trip up a first-time install when you are *not* going through the full CLI wizard.

If you want the fully-automated, CLI-driven path instead, see `INSTALL_UBUNTU_24.04.md` at the repo root.

## Architecture Summary

Busibox is a self-hosted AI platform: document processing, semantic search, RAG agents, custom apps, LLM gateway — all running in Docker (or Proxmox LXC).

- **APIs**: FastAPI (Python 3.11+) — AuthZ, Data, Agent, Search, Docs, Deploy, Embedding
- **Data**: PostgreSQL + RLS, Milvus (vectors), MinIO (S3), Redis, Neo4j (graph)
- **LLM**: LiteLLM gateway → local (vLLM, Ollama) or cloud (OpenAI, Anthropic, Bedrock)
- **Frontend**: Next.js 16 + React 19 (lives in a separate repo: `busibox-frontend`)
- **Reverse proxy**: nginx with SSL
- **Management**: Rust TUI CLI at `cli/busibox/`

Zero Trust auth with RS256 JWTs — no shared secrets between services. Secrets are managed through Ansible Vault with dual-key encryption.

## Prerequisites

Confirmed working on Ubuntu 24.04 with:

- Docker 29+ and Docker Compose v2
- Python 3.11+ (3.12 works)
- Node 20+
- GNU Make 4.3
- Rust toolchain (for the CLI)

Install Rust if missing:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
```

Install Ansible (for vault commands):

```bash
pip3 install --user ansible ansible-core
export PATH="$HOME/.local/bin:$PATH"
```

## Key Gotcha: Secrets Come from the Vault, Not .env

This is the single biggest source of confusion for a first-time Docker install.

The `make docker-up` target reads secrets via `scripts/lib/vault.sh` at runtime — it does not read `.env.dev` directly for the critical values. Specifically, these are pulled from the encrypted Ansible vault:

- `POSTGRES_PASSWORD`
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` (from `secrets.minio.root_user` / `root_password`)
- `AUTHZ_MASTER_KEY`
- `LITELLM_API_KEY`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`
- `NEO4J_PASSWORD`
- `GITHUB_AUTH_TOKEN` (optional)

If the vault is not set up, the `$(eval ...)` lines in the Makefile will fail with `FATAL: Cannot read X from vault` and docker-compose will never start.

You have two choices: set up the vault properly (recommended), or run docker compose directly bypassing the Makefile's vault lookups.

## Path A: Set Up the Vault (Recommended)

This path mirrors what the CLI wizard does for you.

### 1. Create the vault file

```bash
cd ~/maigent-code/busibox
cp provision/ansible/roles/secrets/vars/vault.example.yml \
   provision/ansible/roles/secrets/vars/vault.dev.yml
```

Edit `vault.dev.yml` and replace every `CHANGE_ME_*` value. At minimum:

- `secrets.postgresql.password` — e.g. `devpassword`
- `secrets.minio.root_user` / `root_password` — e.g. `minioadmin` / `minioadmin`
- `secrets.neo4j.password` — e.g. `devpassword`
- `secrets.authz_master_key`, `secrets.jwt_secret`, `secrets.session_secret` — generate with `openssl rand -hex 32`
- `secrets.litellm_api_key`, `secrets.litellm_master_key`, `secrets.litellm_salt_key` — generate each with `openssl rand -hex 32` (prefix as shown in the example)
- `secrets.config_api.encryption_key` — generate with `openssl rand -hex 32`
- `secrets.admin_emails` — your email (first admin)
- `secrets.allowed_email_domains` — `*` for dev

### 2. Encrypt it and save the vault password

```bash
# Encrypt the vault file (you'll choose a vault password)
ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.dev.yml

# Save the vault password where the Makefile expects it
echo "your-vault-password" > ~/.busibox-vault-pass-dev
chmod 600 ~/.busibox-vault-pass-dev
```

The vault password file naming follows `~/.busibox-vault-pass-{env_prefix}`. For `ENV=development` the prefix is `dev`, for `staging` it is `staging`, for `production` it is `prod`.

### 3. Create the dev env file for non-secret settings

```bash
cp env.local.example .env.dev
```

Edit `.env.dev`:

- Set `GITHUB_AUTH_TOKEN` to a PAT with `read:packages` scope (required for `@jazzmind/busibox-app`)
- Set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` if you want cloud LLMs
- The `POSTGRES_PASSWORD`, `MINIO_*`, `AUTHZ_MASTER_KEY`, etc. in this file are **ignored** by `make docker-up` — the vault is the source of truth

### 4. Clone the frontend repo (dev overlay needs it)

```bash
cd ~/maigent-code
git clone https://github.com/jazzmind/busibox-frontend.git
```

The dev overlay (`docker-compose.local-dev.yml`) mounts the frontend monorepo as a volume for Turbopack hot-reload. Without it, the `core-apps` container build will fail.

### 5. Build the CLI (optional but recommended)

```bash
cd ~/maigent-code/busibox/cli/busibox
cargo build --release
# Binary: ./target/release/busibox
```

### 6. Start services

```bash
cd ~/maigent-code/busibox
make docker-up
```

Startup runs in three phases:

1. **Infrastructure** — postgres, redis, minio, etcd, milvus, neo4j
2. **Init** — minio-init (creates buckets), milvus-init (creates collection with hybrid schema)
3. **Everything else** — AuthZ, Data, Agent, Search, Docs, Deploy, Embedding, LiteLLM, nginx, core-apps

Milvus takes ~90s for its first healthcheck to pass, so give it time.

### 7. Verify and access

```bash
make docker-ps
```

| Service | URL |
|---------|-----|
| Portal | `https://localhost/portal` |
| Agents | `https://localhost/agents` |
| Agent API docs | `http://localhost:8000/docs` |
| MinIO console | `http://localhost:9001` |
| PostgreSQL | `localhost:5432` |
| Milvus | `localhost:19530` |

First user to sign up with an email matching `secrets.admin_emails` gets admin access.

## Path B: Bypass the Vault (Quick Hack, Not Recommended)

If you want to run docker-compose directly without setting up the vault, you can export the required variables inline:

```bash
cd ~/maigent-code/busibox
cp env.local.example .env.dev
# Edit .env.dev with real values

# Map env.local.example names to what docker-compose.yml expects
export MINIO_ROOT_USER=minioadmin
export MINIO_ROOT_PASSWORD=minioadmin
export POSTGRES_PASSWORD=devpassword
export NEO4J_PASSWORD=devpassword
export AUTHZ_MASTER_KEY=$(openssl rand -hex 32)
export LITELLM_API_KEY=sk-local-dev-key
export LITELLM_MASTER_KEY=sk-local-dev-key
export LITELLM_SALT_KEY=salt-local-dev-key
export CONTAINER_PREFIX=dev
export COMPOSE_PROJECT_NAME=dev-busibox
export GITHUB_AUTH_TOKEN=ghp_...    # your PAT
export DEV_APPS_DIR=""
export BUSIBOX_HOST_PATH=$(pwd)

docker compose \
  -f docker-compose.yml \
  -f docker-compose.local-dev.yml \
  --env-file .env.dev \
  up -d
```

**Caveats**: `make manage`, `make install SERVICE=...`, and every other orchestration target still need the vault. This path is only useful if you want to eyeball the stack coming up before committing to the full vault setup.

## Common Problems

**`FATAL: Cannot read POSTGRES_PASSWORD from vault`**
Vault or vault password file is missing. Check:

- `provision/ansible/roles/secrets/vars/vault.dev.yml` exists and is encrypted
- `~/.busibox-vault-pass-dev` exists, is readable, and contains the correct password
- `ansible-vault view provision/ansible/roles/secrets/vars/vault.dev.yml --vault-password-file ~/.busibox-vault-pass-dev` returns decrypted YAML

**`core-apps` container fails to build**
Missing `GITHUB_AUTH_TOKEN` or missing `busibox-frontend` checkout. The PAT needs `read:packages` scope to pull `@jazzmind/busibox-app` from GitHub Packages.

**Milvus unhealthy for a long time**
Give it 90+ seconds after etcd and minio are healthy. If it never comes up, check `docker logs dev-milvus` — often it is a permissions issue on the `milvus_data` volume.

**`make docker-up` hangs**
The Makefile's vault `$(eval ...)` lookups run sequentially on every invocation. If any one blocks (e.g., an interactive prompt from `ansible-vault`), the whole target stalls. Make sure the vault password file exists and has the correct permissions.

## Next Steps

- `docs/administrators/03-configure.md` — post-install configuration
- `docs/administrators/05-ai-models.md` — wiring up LLM providers
- `docs/developers/architecture/` — how the pieces fit together
- `TESTING.md` — running tests locally
