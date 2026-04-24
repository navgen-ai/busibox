---
title: `busibox-quick` creates vault from example template but never substitutes the CHANGE_ME placeholders — services start with literal `CHANGE_ME_*` passwords
issue: 008
status: open
severity: critical
area: cli, vault, install
---

# `busibox-quick` leaves `CHANGE_ME_*` placeholders in the vault

## Symptom

After `busibox-quick` finishes, services fail to authenticate. Container env shows literal values like:

```
POSTGRES_PASSWORD=CHANGE_ME_POSTGRES_PASSWORD
MINIO_ROOT_USER=CHANGE_ME_MINIO_ROOT_USER
AUTHZ_MASTER_KEY=CHANGE_ME_AUTHZ_MASTER_KEY_32_BYTES
```

— because the ansible play substitutes `{{ busibox_secrets.* }}` from the vault, and the vault contains the raw `CHANGE_ME_*` strings from the example template.

Ansible's "Warn about placeholder secrets" task logs this at the start of every play:

```
⚠️  WARNING: Some secrets appear to be placeholder values (CHANGE_ME).
   These should be replaced with real values before production use.
   Placeholder values found in:
     - POSTGRES_PASSWORD  - MINIO_ROOT_USER  - MINIO_ROOT_PASSWORD  - AUTHZ_MASTER_KEY  - SSO_JWT_SECRET
```

…but it's a *warning*, not a failure, and install keeps going. Containers come up, but they can't talk to each other.

## Root cause

`cli/busibox-quick/src/main.rs:64` calls `vault::create_vault_from_example(&repo_root, PROFILE_ID, &vault_password)`. That function:

1. Copies `provision/ansible/roles/secrets/vars/vault.example.yml` → `vault.<profile>.yml`.
2. Encrypts the copy with `vault_password` via `ansible-vault`.

It does **not** walk the YAML and substitute any placeholder values. The example file is full of `CHANGE_ME_*` strings that were meant to be filled in by the operator before (or during) install. `busibox-quick` skips that step.

The `vault_password` variable name here is misleading: it's the password *used to encrypt the vault file*, not any of the secrets *inside* it.

## Workaround

Decrypt, sed-replace, re-encrypt:

```bash
cd ~/maigent-code/busibox
export ANSIBLE_VAULT_PASSWORD="$(echo -n <master> | \
  ./cli/target/release/examples/print-vault-password local-development-docker)"
VAULT=provision/ansible/roles/secrets/vars/vault.local-development-docker.yml
ansible-vault decrypt "$VAULT" --vault-password-file scripts/lib/vault-pass-from-env.sh

sed -i \
  -e 's/CHANGE_ME_POSTGRES_PASSWORD/devpassword/g' \
  -e 's/CHANGE_ME_NEO4J_PASSWORD/devpassword/g' \
  -e 's/CHANGE_ME_MINIO_ROOT_USER/minioadmin/g' \
  -e 's/CHANGE_ME_MINIO_ROOT_PASSWORD/minioadmin123/g' \
  # …and every other CHANGE_ME the install uses
  "$VAULT"

ansible-vault encrypt "$VAULT" --vault-password-file scripts/lib/vault-pass-from-env.sh
```

(SMTP/GitHub/SSL-email placeholders can stay as CHANGE_ME for local-dev — **but see issue #015**: leaving SMTP placeholders means the Portal's magic-link login appears to succeed but no email is sent; the 6-digit code is silently written to `dev-bridge-api` logs instead.)

## Proposed fix

1. **`busibox-quick` should generate secrets, not leave placeholders.** For the local-dev profile it should:
   - Auto-generate random 32-byte strings for every `CHANGE_ME_<KEY>_32_BYTES` style key (postgres/minio/neo4j passwords, jwt/authz/session/encryption keys, litellm master/salt keys).
   - Use the admin email it already asked for to populate `admin_emails`.
   - Leave genuinely external placeholders (GitHub PAT, SSL-email, SMTP host) as CHANGE_ME and document that they need to be filled in before those optional features work — but **never** for required-for-boot infra credentials.
2. **Ansible's "Warn about placeholder secrets" should become a fail** for required-for-boot keys when `busibox_env=development` with `docker_dev_mode=local-dev`. The operator can `--skip-tags validate` if they really want.
3. **`busibox-core::vault::create_vault_from_example` should take a `secrets: HashMap<String, String>` argument** so callers can pass substitutions at creation time, and the helper does the walk-and-replace before encryption. That removes the current foot-gun where "create vault" and "populate vault" are two different operations nobody remembers to do.

## References

- `cli/busibox-quick/src/main.rs:64` — `create_vault_from_example` call
- `cli/busibox-core/src/vault.rs:390` — implementation (copy + encrypt only)
- `provision/ansible/roles/secrets/vars/vault.example.yml` — template with `CHANGE_ME_*` everywhere
- `provision/ansible/docker.yml` — "Warn about placeholder secrets" task (warns but doesn't fail)
