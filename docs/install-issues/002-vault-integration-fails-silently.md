---
title: Vault read failure passes empty secrets to docker compose, which then errors with misleading message
issue: 002
status: open
severity: high
area: makefile, vault, scripts
---

# Vault read failure passes empty secrets to `docker compose`

## Symptom

Running `make docker-start SERVICE=authz-api` (or any make target that wraps compose) prints:

```
FATAL: Cannot read POSTGRES_PASSWORD from vault
FATAL: Cannot read MINIO_ROOT_USER from vault
FATAL: Cannot read MINIO_ROOT_PASSWORD from vault
FATAL: Cannot read AUTHZ_MASTER_KEY from vault
...
... POSTGRES_PASSWORD="" MINIO_ACCESS_KEY="" ... docker compose -f ... up -d ...
error while interpolating services.authz-api.environment.POSTGRES_PASSWORD: required variable POSTGRES_PASSWORD is missing a value: POSTGRES_PASSWORD must be set
```

The "FATAL" messages are logged but the Makefile keeps going, substituting empty strings, which only fails at the compose layer with a message that points at the compose file rather than the underlying vault problem.

## Root cause

The make target (see `Makefile:545-553`) builds a one-line command of the form:

```makefile
POSTGRES_PASSWORD="$(POSTGRES_PASSWORD)" ... docker compose ... up -d $(SERVICE)
```

where `$(POSTGRES_PASSWORD)` comes from a vault-read helper. When that helper fails (missing master password, missing profile, missing vault pass file at `~/.busibox-vault-pass-*` or `~/.busibox/vault-keys/*.enc`, or a stale/wrong password), it emits a FATAL log but returns an empty string as the variable value instead of causing `make` to stop.

The compose `${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}` guard catches the empty string, but the resulting error surfaces at the compose layer and reads as if the user forgot to configure something — not as a vault-access failure.

## Workaround

On this install we bypassed the Makefile and ran compose directly:

```bash
env -u POSTGRES_USER -u POSTGRES_PASSWORD \
  CONTAINER_PREFIX=dev COMPOSE_PROJECT_NAME=dev-busibox BUSIBOX_HOST_PATH="$PWD" \
  docker compose -f docker-compose.yml -f docker-compose.local-dev.yml up -d
```

This uses the `.env` file for secrets. For a real fix the vault read should work.

## Proposed fix

1. **Vault helper must be strict.** The helper invoked by `Makefile` (`scripts/lib/vault-pass-from-env.sh` and friends) should exit non-zero on failure, and the Makefile should invoke it with `:=` so a failure aborts `make`. Empty strings should never be substituted into compose.
2. **Better first-time-user error.** The FATAL log should say specifically: "No profile configured — run `busibox` to create one" or "Master password could not decrypt `~/.busibox/vault-keys/$PROFILE.enc`." Right now a brand-new Ubuntu user hits this on their first `make install` and has no idea what a vault is.
3. **Fallback to `.env` for local-dev profile.** Single-machine local-dev installs shouldn't require a vault at all — `.env` should be enough. The vault dependency should kick in for multi-host / remote profiles only.

## References

- `Makefile:545-553` — phase 1/2/3 compose invocations
- `CLAUDE.md` — "NEVER run docker compose directly" (but make is broken too)
- `~/.busibox/vault-keys/local-development-docker.enc` — existing encrypted key with unknown master password on this host
