---
title: Compose project name drifted between `busibox` and `dev-busibox`, leaving orphan volumes and unreusable images
issue: 005
status: open
severity: medium
area: compose, makefile, install-docs
---

# Compose project name drifted between `busibox` and `dev-busibox`

## Symptom

- `docker volume ls` shows two parallel sets: `busibox_*` (15 volumes) and `dev-busibox_*` (17 volumes), for the same data.
- On `make docker-start`, compose warns: `[WARN] Removing stale container dev-user-apps (status=exited, project=busibox, expected=dev-busibox)`.
- Local-built images are tagged `busibox-user-apps:latest` but compose under the current project name expects `dev-busibox-user-apps:latest`, so `docker compose up --no-build user-apps` fails with `No such image: dev-busibox-user-apps:latest` even though `busibox-user-apps:latest` exists and is perfectly usable.

## Root cause

Docker Compose derives the project name from (in order) `COMPOSE_PROJECT_NAME`, `-p`, or the current directory name. It's used to prefix container names, volume names, network names, and locally-built image tags.

At some point between March and April 2026 the busibox Makefile switched its default project name from `busibox` to `dev-busibox` (`CONTAINER_PREFIX=dev` + `COMPOSE_PROJECT_NAME=dev-busibox`). After that switch:

1. Pre-existing volumes under the old name (`busibox_postgres_data`, etc.) were no longer mounted — compose created fresh `dev-busibox_postgres_data`, losing any data unless manually migrated.
2. Pre-built image tags (`busibox-user-apps:latest`) didn't match the new expected tag (`dev-busibox-user-apps:latest`), so `--no-build` invocations failed. This pushed users toward always-rebuild behavior, which is slow.
3. Users who still had the old containers hanging around ended up with the mixed state observed above.

## Workaround

Accept the drift, wipe everything, start over under the new project name. That's what this install did.

## Proposed fix

1. **Pin the project name explicitly and document it.** The compose file itself can set `name: dev-busibox` at the top (compose spec supports this). That removes ambiguity regardless of how the user invokes compose.
2. **Migration note in `INSTALL_UBUNTU_24.04.md`.** If any user is upgrading from an earlier install, they'll hit this. A short "upgrading from March 2026 layout" section should say: expect to wipe volumes, or provide a rename script.
3. **CLI `install` flow should detect orphaned volumes under prior names** and either offer to rename/migrate them (`docker volume create + copy`) or at least warn the user that they're abandoning prior data.

## References

- `Makefile:545-553` — uses `COMPOSE_PROJECT_NAME=dev-busibox`
- `CLAUDE.md` "Manager Container" section — describes `USE_MANAGER` flag which also shapes naming
