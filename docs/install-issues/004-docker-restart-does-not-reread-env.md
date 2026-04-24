---
title: `docker restart` does not re-read .env or compose env vars
issue: 004
status: open
severity: low
area: docs, operator-experience
---

# `docker restart` does not re-read `.env` / compose env vars

## Symptom

After editing `.env` to fix a credential, the user runs:

```bash
docker restart dev-authz-api
```

Nothing changes. The container still has the old, stale env vars. Logs continue to show the same auth failure as before. The user concludes their `.env` edit didn't work, goes in circles.

## Root cause

`docker restart` restarts an existing container with the *same* config it was created with. It does not re-evaluate compose env interpolation or re-read `.env`. You need `docker compose up -d` (which detects config drift and recreates) or explicit `--force-recreate`.

This trips up anyone used to `docker compose` auto-picking up `.env` changes, because in compose 1.x era the UX was closer to that.

## Workaround

```bash
docker compose up -d --force-recreate <service>
```

or full `docker compose down && docker compose up -d`.

## Proposed fix

1. **Troubleshooting doc update.** `INSTALL_UBUNTU_24.04.md` "Services Not Starting" section currently suggests `docker compose restart authz` — should explicitly note that this won't pick up `.env` changes and show the `up -d` recipe.
2. **CLI `manage` action.** The `busibox` CLI's `manage … restart` action should internally use `docker compose up -d` (or `--force-recreate` when it detects drift) so users doing the "correct" thing (`busibox` CLI) never get bitten by this.

## References

- `INSTALL_UBUNTU_24.04.md:430-436` — the troubleshooting example that's misleading.
