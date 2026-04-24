---
title: Stale vault state (encrypted vault file + encrypted master key) from a prior install is not detected by `busibox-quick`, leading to silent corruption
issue: 007
status: open
severity: high
area: cli, vault
---

# Stale vault state is not detected — re-running `busibox-quick` silently mixes old and new credentials

## Symptom

On a machine that had busibox installed previously (or where the user rsync'd a repo that contains `provision/ansible/roles/secrets/vars/vault.<profile>.yml`), re-running `busibox-quick` does not start fresh. Instead:

- `busibox-quick` generates a **new** vault password (`vault::generate_vault_password`) for this session.
- It then checks `has_vault_file(&repo_root, PROFILE_ID)`. If the vault file already exists (encrypted with some **old** vault password the user no longer has), it **skips re-creation** and just reuses it.
- It encrypts the *new* vault password with the *new* master password and writes it to `~/.busibox/vault-keys/<profile>.enc`.
- Now: the encrypted master-key-file claims the vault password is X, but the actual vault file is encrypted with some other password Y. Every subsequent `ansible-vault decrypt` will fail.

Symptom at deploy time: Ansible plays fail with `ERROR! Attempting to decrypt but no vault secrets found` or `Decryption failed on …/vault.<profile>.yml`.

On this machine we found both stale artifacts:

- `provision/ansible/roles/secrets/vars/vault.local-development-docker.yml` (from Mar 27)
- `~/.busibox/vault-keys/local-development-docker.enc` (from Mar 26)

…with no known master password for the old key.

## Root cause

`cli/busibox-quick/src/main.rs:61-68` checks for the vault file but never cross-validates that the encrypted master key in `~/.busibox/vault-keys/` can actually decrypt the vault file. The branch "vault file exists → reuse" is an optimistic assumption that only holds for truly-incremental re-runs on the same machine with the same master password.

## Workaround

Before running `busibox-quick`, manually delete both artifacts:

```bash
rm -f ~/.busibox/vault-keys/local-development-docker.enc
rm -f ~/maigent-code/busibox/provision/ansible/roles/secrets/vars/vault.local-development-docker.yml
```

`busibox-quick` will then re-create both, with a consistent vault password.

If the old master password is known, nothing needs to be deleted — the user can re-encrypt the existing vault file under a new key.

## Proposed fix

1. **Validate before reusing.** When `busibox-quick` sees an existing vault file, it should try to decrypt it with any already-present `~/.busibox/vault-keys/<profile>.enc` by prompting for the master password. If the user can't supply the old master password, it should explicitly prompt: "Existing vault found but cannot be decrypted — start fresh? (y/N)" and, on yes, back up the old files to `*.bak-<timestamp>` and proceed.
2. **Single source of truth for vault state.** Right now "vault exists" is split between the repo (`vault.*.yml`) and the home dir (`~/.busibox/vault-keys/*.enc`). Moving or unifying these would eliminate the partial-state failure mode. The repo file could live under `~/.busibox/` entirely, or the encrypted master-key could live next to the vault file in the repo.
3. **Document the backup/restore flow.** If a user rsyncs a busibox tree between machines, they also need to copy `~/.busibox/vault-keys/` or they'll hit this. That's not mentioned in `INSTALL_UBUNTU_24.04.md` or any obvious doc.

## References

- `cli/busibox-quick/src/main.rs:61-68` — the "has vault file" check
- `cli/busibox-core/src/vault.rs` — vault password generation + file handling
- This machine: both stale artifacts present after rsync from another host without the corresponding `~/.busibox/vault-keys/` content
