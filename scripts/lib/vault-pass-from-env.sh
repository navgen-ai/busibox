#!/bin/bash
# Vault password helper for Ansible.
# Used as --vault-password-file to read the vault password from an
# environment variable instead of a plaintext file on disk.
#
# Usage:
#   export ANSIBLE_VAULT_PASSWORD="secret"
#   export ANSIBLE_VAULT_PASSWORD_FILE=/path/to/this/script
#   ansible-playbook ...
echo "${ANSIBLE_VAULT_PASSWORD:?ANSIBLE_VAULT_PASSWORD must be set}"
