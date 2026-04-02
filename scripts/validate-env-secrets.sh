#!/usr/bin/env bash
# validate-env-secrets.sh — Check that critical secrets are present in a rendered .env file.
#
# Execution context: Runs on target containers via Ansible `script` module.
# Usage: validate-env-secrets.sh <env-file> <KEY1> [KEY2 ...]
#
# Exits 0 if all keys have non-empty, non-placeholder values.
# Exits 1 if any key is missing, empty, or a known placeholder.
set -euo pipefail

ENV_FILE="${1:?Usage: validate-env-secrets.sh <env-file> <KEY1> [KEY2 ...]}"
shift

if [[ ! -f "$ENV_FILE" ]]; then
    echo "FAIL: $ENV_FILE does not exist"
    exit 1
fi

errors=0
for key in "$@"; do
    value=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
    if [[ -z "$value" ]]; then
        echo "FAIL: $key is empty or missing"
        errors=$((errors + 1))
    elif echo "$value" | grep -qi '^CHANGE_ME\|^minioadmin$\|^devpassword$\|^TODO'; then
        echo "FAIL: $key has placeholder value"
        errors=$((errors + 1))
    else
        echo "OK: $key is set (${#value} chars)"
    fi
done

if [[ "$errors" -gt 0 ]]; then
    echo "ERROR: $errors critical secret(s) missing — check vault"
    exit 1
fi
echo "All critical secrets present"
