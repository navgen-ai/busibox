---
title: Leftover POSTGRES_USER/POSTGRES_PASSWORD in ~/.bashrc silently overrides .env
issue: 001
status: open
severity: high
area: install-docs, docker-compose
---

# Leftover `POSTGRES_USER` / `POSTGRES_PASSWORD` in `~/.bashrc` silently overrides `.env`

## Symptom

After `./install_ubuntu_24.sh` and `make install SERVICE=all` (or a `docker compose up`), services such as `authz-api`, `data-worker`, and `agent-api` fail to start with:

```
asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "admin"
```

or similar for user `busibox_user` with whatever random password was in the shell env. The postgres container itself is healthy and `.env` appears correct (`POSTGRES_USER=busibox_user`, `POSTGRES_PASSWORD=devpassword`).

## Root cause

Docker Compose variable interpolation precedence is:

1. Environment variables in the shell running `docker compose`
2. Variables from the `--env-file` or `.env` file
3. Defaults in the compose file (`${VAR:-default}`)

If `~/.bashrc` exports `POSTGRES_USER` / `POSTGRES_PASSWORD` (common when the user has worked on another Postgres project previously), those shell exports win over `.env`. On this machine we found:

```bash
# ~/.bashrc lines 174-175
export POSTGRES_USER='admin'
export POSTGRES_PASSWORD='Jn55G7jQ4xV!NVlf'
```

Those values get baked into every service that uses `${POSTGRES_USER:-busibox_user}` / `${POSTGRES_PASSWORD:?…}`, which is essentially the entire stack.

Worse: once containers are created with these env vars, `docker restart` does **not** re-read the environment. The only way to pick up the corrected values is `docker compose up -d --force-recreate` (or `down` + `up`), which the user typically won't try because from their perspective everything looks right.

## Workaround

Temporarily unset the offending vars for the compose invocation:

```bash
env -u POSTGRES_USER -u POSTGRES_PASSWORD docker compose up -d
```

…or for `make` targets:

```bash
env -u POSTGRES_USER -u POSTGRES_PASSWORD make install SERVICE=all
```

Then recreate any already-bad containers.

## Proposed fix

Several options, in decreasing order of user-friendliness:

1. **Install script adds a preflight check.** `install_ubuntu_24.sh` (and the `busibox` CLI's first-run flow) should explicitly check for `POSTGRES_USER`, `POSTGRES_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `NEO4J_PASSWORD`, and similar exported in the user's env and refuse to proceed / warn loudly, showing the shell rc line number. Offer to comment them out or prompt the user to.
2. **Makefile targets sanitize their own environment** before shelling out to compose. The existing `scripts/make/manager-run.sh` already assembles an explicit environment — it should `env -i` and pass only the vars it wants, instead of inheriting the full shell.
3. **Compose file uses more uniquely-named variables** (e.g. `BUSIBOX_POSTGRES_USER`) so collisions with generic Postgres env from unrelated projects don't happen. This is a bigger refactor but is the durable fix.

## References

- `docker-compose.yml:416` (authz-api POSTGRES_USER)
- Docker Compose env precedence docs: https://docs.docker.com/compose/environment-variables/envvars-precedence/
