---
title: Busibox Ubuntu 24.04 Install Issues
category: install-issues
description: Issues discovered during a fresh install of busibox on Ubuntu 24.04, with root causes, workarounds applied, and proposed permanent fixes. Input for future plans and beads.
published: false
---

# Busibox Ubuntu 24.04 Install Issues

This directory documents issues encountered while installing busibox fresh on an Ubuntu 24.04 host. Each file in this directory captures one issue with:

1. **Symptom** — what the user sees
2. **Root cause** — why it happens
3. **Workaround** — what we did this time to get past it
4. **Proposed fix** — what should change in the repo / docs / install path so the next user doesn't hit it
5. **Scope** — which part of the project owns the fix (install script, ansible role, compose file, docs, CLI, etc.)

These files are the source material for future plans and beads.

## Status legend

- `open` — seen on this install, not fixed in the repo yet
- `workaround-applied` — worked around on the local machine, but the code/docs in the repo still have the bug
- `fixed` — already fixed in the current tree (include the commit/PR ref)

## Start here

**[000 — Summary: what it took to install](000-summary-what-it-took-to-install.md)** — overview of the 15 individual issues grouped into 4 themes, plus the exact sequence of workarounds that produced a working install on 2026-04-23.

## Index

| # | Title | Severity | Status |
|---|-------|----------|--------|
| [001](001-bashrc-postgres-env-sabotages-compose.md) | `~/.bashrc` `POSTGRES_USER`/`POSTGRES_PASSWORD` exports override `.env` | high | open |
| [002](002-vault-integration-fails-silently.md) | Vault read failures pass empty secrets to compose with misleading errors | high | open |
| [003](003-pg-hba-trust-local-scram-tcp-mismatch.md) | `pg_hba.conf` trust-local / scram-TCP hides password drift | medium | open |
| [004](004-docker-restart-does-not-reread-env.md) | `docker restart` does not re-read `.env` — users go in circles | low | open |
| [005](005-compose-project-name-drift.md) | Compose project name drifted `busibox` → `dev-busibox`, orphaning volumes/images | medium | open |
| [006](006-interactive-tty-blocks-automation.md) | `busibox-quick` and TUI read master password from `/dev/tty`, blocking automation | medium | open |
| [007](007-stale-vault-state-not-detected.md) | Stale vault artifacts (repo + home dir) silently mix with new install | high | open |
| [008](008-vault-left-with-change-me-placeholders.md) | `busibox-quick` creates vault but never substitutes `CHANGE_ME_*` placeholders | critical | open |
| [009](009-env-dev-template-never-actually-runs.md) | Ansible `.env.dev` template task never fires; compose gets empty env file | high | open |
| [010](010-busibox-frontend-not-auto-cloned.md) | `core-apps` needs sibling `busibox-frontend` repo; installer never clones it | high | open |
| [011](011-ansible-doesnt-override-postgres-user-env.md) | docker_common sets POSTGRES_PASSWORD in subprocess env but not POSTGRES_USER | medium | open |
| [012](012-compose-validates-profile-gated-vars.md) | `${VLLM_MODEL:?…}` on a profile-gated service breaks every non-vllm install | high | open |
| [013](013-rsync-leftovers-macos-binary.md) | rsync from macOS dev machine drops Mach-O + stray files at repo root | low | workaround-applied |
| [014](014-install-docs-contradict-claude-md.md) | Install docs tell users to run `docker compose` directly; `CLAUDE.md` forbids it | medium | open |
| [015](015-smtp-placeholders-block-login-silently.md) | SMTP placeholders make login silently impossible; 6-digit code hides in `bridge-api` logs | medium | workaround-applied |

More will be added as the install progresses.
