---
title: `INSTALL_UBUNTU_24.04.md` and `install_part2_user.sh` tell users to run `docker compose` directly; `CLAUDE.md` says never do that
issue: 014
status: open
severity: medium
area: docs, operator-experience
---

# Install docs contradict `CLAUDE.md` — users get pointed at `docker compose up -d`

## Symptom

A new Ubuntu 24.04 operator follows `INSTALL_UBUNTU_24.04.md` step-by-step. Multiple sections tell them to run `docker compose` directly:

- `INSTALL_UBUNTU_24.04.md:346` — "Starting Services: `cd ~/maigent-code/busibox && docker compose up -d`"
- `INSTALL_UBUNTU_24.04.md:359` — "Stopping Services: `docker compose down`"
- `INSTALL_UBUNTU_24.04.md:370-377` — "Viewing Logs: `docker compose logs -f …`"
- `INSTALL_UBUNTU_24.04.md:392-393` — "Redeploy services: `docker compose down && docker compose up -d --build`"
- `install_part2_user.sh:150-152` — post-install message prints "Or deploy using Docker Compose: `docker compose up -d`"

`CLAUDE.md` (read by AI agents) is emphatic in the opposite direction:

> **NEVER run `docker compose`, `docker`, or `ansible-playbook` commands directly.**

An operator following the install guide will wire their muscle memory to `docker compose up -d`. When they later try to get AI assistance on the repo, the AI rightly refuses to touch compose directly and steers them to `make install` / `busibox` CLI — only to find (see issues #002, #006, #009) that those paths are themselves broken for fresh installs.

## Root cause

Install docs were authored before the CLI / make / vault workflow was mandated. The docs were not updated when `CLAUDE.md`'s "NEVER run docker compose directly" rule was introduced. The two sources now disagree.

Operationally, `docker compose` can work — as long as `.env` has every required variable, no `${VAR:?}` interpolation trips (issue #012), shell env doesn't leak unwanted POSTGRES_* (issue #001/#011), and the vault-sourced secrets have been exported manually. But that's a lot of "as long as," and none of it is in the install doc.

## Workaround

Ignore the conflicting doc sections for now. Use the CLI path when it works, drop down to `env -u POSTGRES_USER -u POSTGRES_PASSWORD ansible-playbook …` with `ANSIBLE_VAULT_PASSWORD` set when the CLI blocks you on interactive prompts.

## Proposed fix

Rewrite the "Common Operations" and "Troubleshooting" sections of `INSTALL_UBUNTU_24.04.md` so every `docker compose` example becomes a `busibox`-CLI or `make manage` example. Same for `install_part2_user.sh`'s post-install message — point at `busibox` only.

Secondary: decide deliberately what operators should do when the CLI is unavailable or fails. If `make install SERVICE=…` is the fallback, document it (including the `ANSIBLE_VAULT_PASSWORD` env var) in one place. Right now the knowledge is split between `CLAUDE.md`, `scripts/lib/vault.sh` source, and tribal memory.

## References

- `CLAUDE.md` (top) — "CRITICAL: Service Operations — NEVER run docker compose…"
- `INSTALL_UBUNTU_24.04.md:346,359,370-377,392-393` — examples that violate the above
- `install_part2_user.sh:150-152` — end-of-install message
