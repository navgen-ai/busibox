#!/usr/bin/env bash
#
# Vault Access Library for Busibox
#
# Description:
#   Provides functions for accessing Ansible vault secrets from make install/update.
#   Used by both Docker and Proxmox deployments to access encrypted secrets.
#
# Usage:
#   source scripts/lib/vault.sh
#   ensure_vault_access
#   get_vault_secret "secrets.postgresql.password"
#
# Dependencies: ansible-vault, yq (optional, for structured reading)

# Vault file locations (relative to REPO_ROOT)
VAULT_FILE="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/provision/ansible/roles/secrets/vars/vault.yml"
VAULT_EXAMPLE="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/provision/ansible/roles/secrets/vars/vault.example.yml"

# Colors (use existing if available, or define minimal set)
_V_RED="${RED:-\033[0;31m}"
_V_GREEN="${GREEN:-\033[0;32m}"
_V_YELLOW="${YELLOW:-\033[1;33m}"
_V_BLUE="${BLUE:-\033[0;34m}"
_V_NC="${NC:-\033[0m}"

# Logging functions (use existing if available)
_vault_info() {
    if type info &>/dev/null; then
        info "$1"
    else
        echo -e "${_V_BLUE}[INFO]${_V_NC} $1"
    fi
}

_vault_warn() {
    if type warn &>/dev/null; then
        warn "$1"
    else
        echo -e "${_V_YELLOW}[WARNING]${_V_NC} $1"
    fi
}

_vault_error() {
    if type error &>/dev/null; then
        error "$1"
    else
        echo -e "${_V_RED}[ERROR]${_V_NC} $1"
    fi
}

_vault_success() {
    if type success &>/dev/null; then
        success "$1"
    else
        echo -e "${_V_GREEN}[SUCCESS]${_V_NC} $1"
    fi
}

# Check if ansible-vault is available
check_ansible_vault() {
    if ! command -v ansible-vault &>/dev/null; then
        _vault_error "ansible-vault not found. Please install Ansible:"
        echo "  pip install ansible"
        return 1
    fi
    return 0
}

# Check if vault file exists
check_vault_file() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        return 1
    fi
    return 0
}

# Check if vault file is encrypted
is_vault_encrypted() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        return 1
    fi
    
    if head -1 "$VAULT_FILE" | grep -q '^\$ANSIBLE_VAULT'; then
        return 0
    fi
    return 1
}

# Create vault from example if it doesn't exist
create_vault_from_example() {
    if [[ ! -f "$VAULT_EXAMPLE" ]]; then
        _vault_error "Vault example file not found: $VAULT_EXAMPLE"
        return 1
    fi
    
    _vault_info "Creating vault file from example..."
    cp "$VAULT_EXAMPLE" "$VAULT_FILE"
    
    _vault_warn "Vault file created but NOT encrypted."
    echo "  Please edit with your secrets and encrypt:"
    echo "    1. Edit: $VAULT_FILE"
    echo "    2. Encrypt: ansible-vault encrypt $VAULT_FILE"
    return 0
}

# Encrypt vault file
encrypt_vault() {
    if [[ ! -f "$VAULT_FILE" ]]; then
        _vault_error "Vault file not found"
        return 1
    fi
    
    if is_vault_encrypted; then
        _vault_info "Vault file is already encrypted"
        return 0
    fi
    
    _vault_info "Encrypting vault file..."
    if ansible-vault encrypt "$VAULT_FILE"; then
        _vault_success "Vault file encrypted"
        return 0
    else
        _vault_error "Failed to encrypt vault file"
        return 1
    fi
}

# Ensure vault password is accessible
# Sets ANSIBLE_VAULT_PASSWORD_FILE environment variable
ensure_vault_access() {
    local vault_pass_file="$HOME/.vault_pass"
    
    # Check ansible-vault is available
    if ! check_ansible_vault; then
        return 1
    fi
    
    # Check if vault file exists
    if ! check_vault_file; then
        _vault_warn "Vault file not found: $VAULT_FILE"
        
        # Offer to create from example
        if [[ -f "$VAULT_EXAMPLE" ]]; then
            echo ""
            read -p "Create vault from example? (y/N) " create_vault
            if [[ "$create_vault" =~ ^[Yy]$ ]]; then
                create_vault_from_example || return 1
            else
                _vault_error "Vault file required. Create it manually:"
                echo "  cp $VAULT_EXAMPLE $VAULT_FILE"
                echo "  # Edit with your secrets"
                echo "  ansible-vault encrypt $VAULT_FILE"
                return 1
            fi
        else
            _vault_error "Neither vault file nor example found"
            return 1
        fi
    fi
    
    # Check if vault is encrypted
    if ! is_vault_encrypted; then
        _vault_warn "Vault file is not encrypted!"
        echo ""
        read -p "Encrypt vault file now? (Y/n) " encrypt_now
        if [[ ! "$encrypt_now" =~ ^[Nn]$ ]]; then
            encrypt_vault || return 1
        else
            _vault_warn "Continuing with unencrypted vault (not recommended)"
            # No password needed for unencrypted file
            return 0
        fi
    fi
    
    # Use existing password file if available
    if [[ -f "$vault_pass_file" ]]; then
        _vault_info "Using vault password from ~/.vault_pass"
        export ANSIBLE_VAULT_PASSWORD_FILE="$vault_pass_file"
        
        # Verify password works
        if ! ansible-vault view "$VAULT_FILE" &>/dev/null; then
            _vault_error "Vault password in ~/.vault_pass is incorrect"
            rm -f "$vault_pass_file"
            # Fall through to prompt
        else
            return 0
        fi
    fi
    
    # Prompt for password
    local max_attempts=3
    local attempt=1
    
    while [[ $attempt -le $max_attempts ]]; do
        echo -n "Enter Ansible vault password: "
        read -s vault_pass
        echo ""
        
        if [[ -z "$vault_pass" ]]; then
            _vault_error "Password cannot be empty"
            ((attempt++))
            continue
        fi
        
        # Create temporary password file
        local tmp_pass=$(mktemp)
        echo "$vault_pass" > "$tmp_pass"
        chmod 600 "$tmp_pass"
        export ANSIBLE_VAULT_PASSWORD_FILE="$tmp_pass"
        
        # Verify password works
        if ansible-vault view "$VAULT_FILE" &>/dev/null; then
            # Password is correct - offer to save it
            echo ""
            read -p "Save password to ~/.vault_pass for future use? (y/N) " save_pass
            if [[ "$save_pass" =~ ^[Yy]$ ]]; then
                echo "$vault_pass" > "$vault_pass_file"
                chmod 600 "$vault_pass_file"
                _vault_info "Password saved to ~/.vault_pass"
                export ANSIBLE_VAULT_PASSWORD_FILE="$vault_pass_file"
                rm -f "$tmp_pass"
            else
                # Clean up temp file on exit
                trap "rm -f $tmp_pass" EXIT
            fi
            return 0
        else
            _vault_error "Incorrect vault password (attempt $attempt/$max_attempts)"
            rm -f "$tmp_pass"
            ((attempt++))
        fi
    done
    
    _vault_error "Too many incorrect password attempts"
    return 1
}

# Read a secret from the vault
# Usage: get_vault_secret "secrets.postgresql.password"
# Returns: The secret value, or empty string on error
get_vault_secret() {
    local key_path="$1"
    
    if [[ -z "$key_path" ]]; then
        _vault_error "Key path required"
        return 1
    fi
    
    # Ensure we have vault access
    if [[ -z "$ANSIBLE_VAULT_PASSWORD_FILE" ]]; then
        if ! is_vault_encrypted; then
            # Unencrypted - read directly
            :
        else
            _vault_error "Vault access not initialized. Call ensure_vault_access first."
            return 1
        fi
    fi
    
    # Convert dot notation to yq path (e.g., secrets.postgresql.password -> .secrets.postgresql.password)
    local yq_path=".$key_path"
    
    # Check if yq is available
    if command -v yq &>/dev/null; then
        if is_vault_encrypted; then
            ansible-vault view "$VAULT_FILE" 2>/dev/null | yq -r "$yq_path" 2>/dev/null
        else
            yq -r "$yq_path" "$VAULT_FILE" 2>/dev/null
        fi
    else
        # Fallback: use grep/sed for simple key extraction
        # This only works for simple keys, not nested structures
        local simple_key="${key_path##*.}"
        if is_vault_encrypted; then
            ansible-vault view "$VAULT_FILE" 2>/dev/null | grep -E "^[[:space:]]*${simple_key}:" | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '"'"'"
        else
            grep -E "^[[:space:]]*${simple_key}:" "$VAULT_FILE" | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '"'"'"
        fi
    fi
}

# Check if a secret exists and has a non-placeholder value
# Usage: has_vault_secret "secrets.postgresql.password"
# Returns: 0 if secret exists and is not a placeholder, 1 otherwise
has_vault_secret() {
    local key_path="$1"
    local value
    
    value=$(get_vault_secret "$key_path")
    
    if [[ -z "$value" ]]; then
        return 1
    fi
    
    # Check for placeholder values
    if [[ "$value" == "CHANGE_ME"* ]] || \
       [[ "$value" == "your-"* ]] || \
       [[ "$value" == "TODO"* ]] || \
       [[ "$value" == "null" ]] || \
       [[ "$value" == "~" ]]; then
        return 1
    fi
    
    return 0
}

# Validate that required bootstrap secrets exist
# These are secrets that MUST be in the vault before installation/update
validate_vault_secrets() {
    local required_secrets=(
        "secrets.postgresql.password"
        "secrets.minio.root_user"
        "secrets.minio.root_password"
        "secrets.jwt_secret"
        "secrets.better_auth_secret"
    )
    
    local optional_secrets=(
        "secrets.authz_admin_token"
        "secrets.authz_master_key"
        "secrets.litellm_api_key"
        "secrets.encryption_key"
    )
    
    # Ensure vault access first
    if ! ensure_vault_access; then
        return 1
    fi
    
    local missing=()
    local unconfigured=()
    
    echo ""
    _vault_info "Validating required vault secrets..."
    
    for key in "${required_secrets[@]}"; do
        local value=$(get_vault_secret "$key")
        local short_key="${key##*.}"
        
        if [[ -z "$value" ]]; then
            missing+=("$key")
            echo -e "  ${_V_RED}✗${_V_NC} $short_key - missing"
        elif ! has_vault_secret "$key"; then
            unconfigured+=("$key")
            echo -e "  ${_V_YELLOW}○${_V_NC} $short_key - placeholder value"
        else
            echo -e "  ${_V_GREEN}✓${_V_NC} $short_key - configured"
        fi
    done
    
    echo ""
    _vault_info "Checking optional secrets..."
    
    for key in "${optional_secrets[@]}"; do
        local value=$(get_vault_secret "$key")
        local short_key="${key##*.}"
        
        if [[ -z "$value" ]] || ! has_vault_secret "$key"; then
            echo -e "  ${_V_YELLOW}○${_V_NC} $short_key - not configured (optional)"
        else
            echo -e "  ${_V_GREEN}✓${_V_NC} $short_key - configured"
        fi
    done
    
    echo ""
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        _vault_error "Missing required secrets:"
        for key in "${missing[@]}"; do
            echo "  - $key"
        done
        echo ""
        echo "Add these to your vault file: $VAULT_FILE"
        return 1
    fi
    
    if [[ ${#unconfigured[@]} -gt 0 ]]; then
        _vault_error "Secrets with placeholder values:"
        for key in "${unconfigured[@]}"; do
            echo "  - $key"
        done
        echo ""
        echo "Update these in your vault file: $VAULT_FILE"
        echo "Then re-encrypt: ansible-vault encrypt $VAULT_FILE"
        return 1
    fi
    
    _vault_success "All required vault secrets validated"
    return 0
}

# Generate a random secret (for initial setup)
generate_secret() {
    local length="${1:-32}"
    openssl rand -base64 "$length" | tr -d '/+=' | head -c "$length"
}

# Generate all required secrets and update vault (interactive)
setup_vault_secrets() {
    if ! ensure_vault_access; then
        return 1
    fi
    
    _vault_info "Setting up vault secrets..."
    echo ""
    echo "This will generate random values for unconfigured secrets."
    echo "You can edit the vault file later to customize."
    echo ""
    read -p "Continue? (y/N) " continue_setup
    
    if [[ ! "$continue_setup" =~ ^[Yy]$ ]]; then
        return 1
    fi
    
    # This is a placeholder - actual implementation would:
    # 1. Decrypt vault
    # 2. Update YAML with generated secrets
    # 3. Re-encrypt vault
    
    _vault_warn "Interactive secret generation not yet implemented."
    echo "Please edit the vault file manually:"
    echo "  1. Decrypt: ansible-vault decrypt $VAULT_FILE"
    echo "  2. Edit: $VAULT_FILE"
    echo "  3. Encrypt: ansible-vault encrypt $VAULT_FILE"
    
    return 1
}
