#!/usr/bin/env bash
#
# Check Secrets Sync Between Vault and Deployed Containers
#
# Execution Context: Admin workstation (where ansible-vault + vault password are available)
#   For Proxmox: SSHs to the host to read container env files via pct exec
#   For Docker: reads env vars via docker exec (local or remote)
#
# Dependencies: ansible-vault, python3 (with PyYAML), ssh (for Proxmox)
#
# Usage:
#   bash scripts/check/secrets-sync.sh --backend proxmox|docker [--prefix PREFIX] \
#       [--staging] [--vault-prefix VP] [--ssh-host HOST] [--ssh-user USER] [--ssh-key KEY]
#
# Output (JSON to stdout):
#   { "services": { "authz": { "status": "synced", "mismatched": [] }, ... } }
# Debug output goes to stderr (lines prefixed with [DEBUG]).

set -eo pipefail
unset ENV BASH_ENV 2>/dev/null || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BACKEND="${BUSIBOX_BACKEND:-proxmox}"
PREFIX="${CONTAINER_PREFIX:-prod}"
IS_STAGING=false
VAULT_PREFIX="${VAULT_PREFIX:-}"
SSH_HOST=""
SSH_USER="root"
SSH_KEY=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend) BACKEND="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --staging) IS_STAGING=true; shift ;;
        --vault-prefix) VAULT_PREFIX="$2"; shift 2 ;;
        --ssh-host) SSH_HOST="$2"; shift 2 ;;
        --ssh-user) SSH_USER="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# ── Vault decryption (runs LOCALLY on admin workstation) ──────────────────────

VAULT_DIR="${REPO_ROOT}/provision/ansible/roles/secrets/vars"

find_vault_file() {
    if [[ -n "$VAULT_PREFIX" && -f "${VAULT_DIR}/vault.${VAULT_PREFIX}.yml" ]]; then
        echo "${VAULT_DIR}/vault.${VAULT_PREFIX}.yml"; return
    fi
    for candidate in "$VAULT_PREFIX" prod production; do
        [[ -z "$candidate" ]] && continue
        local f="${VAULT_DIR}/vault.${candidate}.yml"
        [[ -f "$f" ]] && echo "$f" && return
    done
    for f in "${VAULT_DIR}"/vault.*.yml; do
        [[ "$f" == *"example"* ]] && continue
        [[ -f "$f" ]] && echo "$f" && return
    done
    return 1
}

find_vault_password() {
    [[ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]] && echo "$ANSIBLE_VAULT_PASSWORD" && return
    for f in ~/.busibox-vault-pass-*; do
        [[ -f "$f" ]] && cat "$f" && return
    done
    [[ -f ~/.vault_pass ]] && cat ~/.vault_pass && return
    return 1
}

VAULT_FILE=$(find_vault_file) || { echo '{"error":"no vault file found"}'; exit 0; }
VAULT_PASS=$(find_vault_password) || { echo '{"error":"no vault password available"}'; exit 0; }

VAULT_YAML=$(echo "$VAULT_PASS" | ansible-vault view "$VAULT_FILE" --vault-password-file=/dev/stdin 2>/dev/null) \
    || { echo '{"error":"vault decryption failed"}'; exit 0; }

vault_get() {
    python3 -c "
import yaml, sys
data = yaml.safe_load(sys.stdin)
keys = '$1'.split('.')
val = data
for k in keys:
    if isinstance(val, dict): val = val.get(k, '')
    else: val = ''; break
print(str(val).strip() if val else '', end='')
" <<< "$VAULT_YAML"
}

V_PG_PASS=$(vault_get "secrets.postgresql.password")
V_MINIO_ROOT_USER=$(vault_get "secrets.minio.root_user")
V_MINIO_ROOT_PASS=$(vault_get "secrets.minio.root_password")
V_JWT_SECRET=$(vault_get "secrets.jwt_secret")
V_AUTHZ_MASTER_KEY=$(vault_get "secrets.authz_master_key")
V_LITELLM_MASTER_KEY=$(vault_get "secrets.litellm_master_key")
V_LITELLM_SALT_KEY=$(vault_get "secrets.litellm_salt_key")
V_GITHUB_TOKEN=$(vault_get "secrets.github.personal_access_token")
V_NEO4J_PASS=$(vault_get "secrets.neo4j.password")
V_CONFIG_ENCRYPTION_KEY=$(vault_get "secrets.encryption_key")

# ── Container IDs ─────────────────────────────────────────────────────────────

CT_AUTHZ=210; CT_AGENT=202; CT_DATA=206; CT_SEARCH=204; CT_DEPLOY=210
CT_LITELLM=207; CT_CORE_APPS=201; CT_BRIDGE=211
CT_MINIO=205; CT_PG=203; CT_CONFIG=210

if $IS_STAGING; then
    CT_AUTHZ=$((CT_AUTHZ+100)); CT_AGENT=$((CT_AGENT+100)); CT_DATA=$((CT_DATA+100))
    CT_SEARCH=$((CT_SEARCH+100)); CT_DEPLOY=$((CT_DEPLOY+100))
    CT_LITELLM=$((CT_LITELLM+100)); CT_CORE_APPS=$((CT_CORE_APPS+100))
    CT_BRIDGE=$((CT_BRIDGE+100)); CT_MINIO=$((CT_MINIO+100)); CT_PG=$((CT_PG+100))
    CT_CONFIG=$((CT_CONFIG+100))
fi

# ── Remote command execution ──────────────────────────────────────────────────

# PATH preamble: ensures /usr/local/bin, /opt/homebrew/bin, etc. are in PATH
# for non-interactive SSH sessions (macOS doesn't source .profile for ssh commands).
# Also handles macOS Docker config quirks (credsStore/currentContext).
REMOTE_PATH_PREAMBLE='for d in /usr/local/bin /opt/homebrew/bin "$HOME/.local/bin"; do [ -d "$d" ] && export PATH="$d:$PATH"; done; '

ssh_cmd() {
    local cmd="$1"
    if [[ -n "$SSH_HOST" ]]; then
        local ssh_args=(-o StrictHostKeyChecking=no -o ConnectTimeout=5)
        [[ -n "$SSH_KEY" ]] && ssh_args+=(-i "$SSH_KEY")
        ssh "${ssh_args[@]}" "${SSH_USER}@${SSH_HOST}" "${REMOTE_PATH_PREAMBLE}${cmd}" 2>/dev/null
    else
        bash -c "$cmd" 2>/dev/null
    fi
}

# Read an env var value from a Proxmox container's env file.
# Handles: KEY=value, KEY="value", KEY='value'
read_proxmox_var() {
    local ctid="$1" env_file="$2" var_name="$3"
    local raw
    raw=$(ssh_cmd "pct status $ctid 2>/dev/null | grep -q running && pct exec $ctid -- grep -E '^${var_name}=' '${env_file}' 2>/dev/null | head -1" || true)
    # Strip KEY= prefix
    local val="${raw#*=}"
    # Strip surrounding double or single quotes
    if [[ "$val" == \"*\" ]]; then val="${val#\"}"; val="${val%\"}"; fi
    if [[ "$val" == \'*\' ]]; then val="${val#\'}"; val="${val%\'}"; fi
    # Strip carriage return
    val="${val//$'\r'/}"
    echo -n "$val"
}

# Check if a value is embedded in a DATABASE_URL-style connection string.
# Usage: check_password_in_url <ctid> <env_file> <var_name> <expected_password>
# Returns 0 if the password appears in the URL, 1 otherwise.
check_password_in_url() {
    local ctid="$1" env_file="$2" var_name="$3" expected="$4"
    local raw
    raw=$(ssh_cmd "pct status $ctid 2>/dev/null | grep -q running && pct exec $ctid -- grep -E '^${var_name}=' '${env_file}' 2>/dev/null | head -1" || true)
    # Check if expected password appears in the URL string
    [[ "$raw" == *"$expected"* ]] && return 0 || return 1
}

is_ct_running() {
    local ctid="$1"
    ssh_cmd "pct status $ctid 2>/dev/null | grep -q running" && return 0 || return 1
}

# ── Docker helpers ────────────────────────────────────────────────────────────

read_docker_var() {
    local container="$1" var_name="$2"
    ssh_cmd "docker exec '$container' printenv '$var_name' 2>/dev/null" || echo ""
}

is_docker_running() {
    local container="$1"
    ssh_cmd "docker ps --filter 'name=^${container}\$' --filter status=running --format '{{.Names}}' 2>/dev/null | grep -q ." && return 0 || return 1
}

# ── Check functions ───────────────────────────────────────────────────────────

TMPOUT=$(mktemp)
trap 'rm -f "$TMPOUT"' EXIT
exec 3>"$TMPOUT"
first_entry=true
emit_sep() { $first_entry || echo -n "," >&3; first_entry=false; }

# check_proxmox <name> <ctid> <env_file> checks...
# Each check is one of:
#   VAR=expected         — exact match: grep VAR from env file, compare value
#   ~URL_VAR=expected    — embedded match: check if expected appears inside URL_VAR's value
check_proxmox() {
    local name="$1" ctid="$2" env_file="$3"; shift 3
    if ! is_ct_running "$ctid"; then
        echo -n "\"${name}\":{\"status\":\"down\",\"mismatched\":[]}" >&3
        return
    fi
    local mismatched="" sep=""
    for check in "$@"; do
        local mode="exact"
        if [[ "$check" == ~* ]]; then
            mode="embedded"; check="${check#\~}"
        fi
        local var_name="${check%%=*}" expected="${check#*=}"
        [[ -z "$expected" ]] && continue

        if [[ "$mode" == "embedded" ]]; then
            if ! check_password_in_url "$ctid" "$env_file" "$var_name" "$expected"; then
                echo "[DEBUG] $name/$var_name(embedded): NOT FOUND in URL" >&2
                mismatched+="${sep}\"${var_name}\""; sep=","
            fi
        else
            local actual
            actual=$(read_proxmox_var "$ctid" "$env_file" "$var_name")
            if [[ "$actual" != "$expected" ]]; then
                echo "[DEBUG] $name/$var_name: expected(${#expected})='${expected:0:6}...' actual(${#actual})='${actual:0:6}...'" >&2
                mismatched+="${sep}\"${var_name}\""; sep=","
            fi
        fi
    done
    if [[ -z "$mismatched" ]]; then
        echo -n "\"${name}\":{\"status\":\"synced\",\"mismatched\":[]}" >&3
    else
        echo -n "\"${name}\":{\"status\":\"mismatch\",\"mismatched\":[${mismatched}]}" >&3
    fi
}

check_docker() {
    local name="$1" container="$2"; shift 2
    if ! is_docker_running "$container"; then
        echo -n "\"${name}\":{\"status\":\"down\",\"mismatched\":[]}" >&3
        return
    fi
    local mismatched="" sep=""
    for check in "$@"; do
        local mode="exact"
        if [[ "$check" == ~* ]]; then
            mode="embedded"; check="${check#\~}"
        fi
        local var_name="${check%%=*}" expected="${check#*=}"
        [[ -z "$expected" ]] && continue

        if [[ "$mode" == "embedded" ]]; then
            local actual
            actual=$(read_docker_var "$container" "$var_name")
            if [[ "$actual" != *"$expected"* ]]; then
                mismatched+="${sep}\"${var_name}\""; sep=","
            fi
        else
            local actual
            actual=$(read_docker_var "$container" "$var_name")
            if [[ "$actual" != "$expected" ]]; then
                mismatched+="${sep}\"${var_name}\""; sep=","
            fi
        fi
    done
    if [[ -z "$mismatched" ]]; then
        echo -n "\"${name}\":{\"status\":\"synced\",\"mismatched\":[]}" >&3
    else
        echo -n "\"${name}\":{\"status\":\"mismatch\",\"mismatched\":[${mismatched}]}" >&3
    fi
}

# ── Run checks ────────────────────────────────────────────────────────────────
# Map env var names to what each service ACTUALLY has in its env file.
# This must match the Ansible role templates exactly.

if [[ "$BACKEND" == "proxmox" ]]; then

    # authz (CT 210): /srv/authz/.env
    #   POSTGRES_PASSWORD=..., AUTHZ_MASTER_KEY=..., AUTHZ_KEY_ENCRYPTION_PASSPHRASE=...
    emit_sep; check_proxmox authz "$CT_AUTHZ" "/srv/authz/.env" \
        "POSTGRES_PASSWORD=$V_PG_PASS" \
        "AUTHZ_MASTER_KEY=$V_AUTHZ_MASTER_KEY"

    # agent (CT 202): /srv/agent/.env
    #   DATABASE_URL=postgresql+asyncpg://user:PASSWORD@host/db (password embedded in URL)
    #   LITELLM_API_KEY=...
    emit_sep; check_proxmox agent "$CT_AGENT" "/srv/agent/.env" \
        "~DATABASE_URL=$V_PG_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    # data + data-worker (CT 206): /srv/data/.env (single env file shared by both)
    #   POSTGRES_PASSWORD, MINIO_SECRET_KEY, LITELLM_API_KEY
    emit_sep; check_proxmox data "$CT_DATA" "/srv/data/.env" \
        "POSTGRES_PASSWORD=$V_PG_PASS" \
        "MINIO_SECRET_KEY=$V_MINIO_ROOT_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    # data-worker shares the same env file; emit the same status
    emit_sep; check_proxmox data-worker "$CT_DATA" "/srv/data/.env" \
        "POSTGRES_PASSWORD=$V_PG_PASS" \
        "MINIO_SECRET_KEY=$V_MINIO_ROOT_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    # search (CT 204): /opt/search/.env
    #   POSTGRES_PASSWORD, NEO4J_PASSWORD, LITELLM_API_KEY
    emit_sep; check_proxmox search "$CT_SEARCH" "/opt/search/.env" \
        "POSTGRES_PASSWORD=$V_PG_PASS" \
        "NEO4J_PASSWORD=$V_NEO4J_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    # deploy (CT 210): /opt/deploy/.env
    #   POSTGRES_ADMIN_PASSWORD, LITELLM_MASTER_KEY
    emit_sep; check_proxmox deploy "$CT_DEPLOY" "/opt/deploy/.env" \
        "POSTGRES_ADMIN_PASSWORD=$V_PG_PASS" \
        "LITELLM_MASTER_KEY=$V_LITELLM_MASTER_KEY"

    # config (CT 210): /opt/config/.env
    #   POSTGRES_PASSWORD, CONFIG_ENCRYPTION_KEY
    emit_sep; check_proxmox config "$CT_CONFIG" "/opt/config/.env" \
        "POSTGRES_PASSWORD=$V_PG_PASS" \
        "CONFIG_ENCRYPTION_KEY=$V_CONFIG_ENCRYPTION_KEY"

    # litellm (CT 207): /etc/default/litellm
    #   LITELLM_MASTER_KEY, LITELLM_SALT_KEY
    emit_sep; check_proxmox litellm "$CT_LITELLM" "/etc/default/litellm" \
        "LITELLM_MASTER_KEY=$V_LITELLM_MASTER_KEY" \
        "LITELLM_SALT_KEY=$V_LITELLM_SALT_KEY"

    # minio (CT 205): /srv/minio/.env
    #   MINIO_ROOT_USER, MINIO_ROOT_PASSWORD
    emit_sep; check_proxmox minio "$CT_MINIO" "/srv/minio/.env" \
        "MINIO_ROOT_USER=$V_MINIO_ROOT_USER" \
        "MINIO_ROOT_PASSWORD=$V_MINIO_ROOT_PASS"

    # portal (CT 201): /srv/apps/busibox-portal/.env
    #   App env template wraps values in quotes: SSO_JWT_SECRET="value"
    emit_sep; check_proxmox portal "$CT_CORE_APPS" "/srv/apps/busibox-portal/.env" \
        "SSO_JWT_SECRET=$V_JWT_SECRET"

else
    # Docker mode — env vars are injected via docker compose, no quoting issues
    P="${PREFIX}-"
    emit_sep; check_docker authz "${P}authz-api" \
        "AUTHZ_MASTER_KEY=$V_AUTHZ_MASTER_KEY" "POSTGRES_PASSWORD=$V_PG_PASS"

    emit_sep; check_docker agent "${P}agent-api" \
        "~DATABASE_URL=$V_PG_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    emit_sep; check_docker data "${P}data-api" \
        "POSTGRES_PASSWORD=$V_PG_PASS" "MINIO_SECRET_KEY=$V_MINIO_ROOT_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    emit_sep; check_docker search "${P}search-api" \
        "POSTGRES_PASSWORD=$V_PG_PASS" "NEO4J_PASSWORD=$V_NEO4J_PASS" \
        "LITELLM_API_KEY=$V_LITELLM_MASTER_KEY"

    emit_sep; check_docker deploy "${P}deploy-api" \
        "POSTGRES_PASSWORD=$V_PG_PASS" "LITELLM_MASTER_KEY=$V_LITELLM_MASTER_KEY"

    emit_sep; check_docker config "${P}config-api" \
        "POSTGRES_PASSWORD=$V_PG_PASS" "CONFIG_ENCRYPTION_KEY=$V_CONFIG_ENCRYPTION_KEY"

    emit_sep; check_docker litellm "${P}litellm" \
        "LITELLM_MASTER_KEY=$V_LITELLM_MASTER_KEY" "LITELLM_SALT_KEY=$V_LITELLM_SALT_KEY"

    emit_sep; check_docker portal "${P}core-apps" \
        "SSO_JWT_SECRET=$V_JWT_SECRET" "GITHUB_AUTH_TOKEN=$V_GITHUB_TOKEN"

    emit_sep; check_docker postgres "${P}postgres" \
        "POSTGRES_PASSWORD=$V_PG_PASS"

    emit_sep; check_docker minio "${P}minio" \
        "MINIO_ROOT_USER=$V_MINIO_ROOT_USER" \
        "MINIO_ROOT_PASSWORD=$V_MINIO_ROOT_PASS"
fi

exec 3>&-
echo -n '{"services":{'
cat "$TMPOUT"
echo '}}'
