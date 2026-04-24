---
title: `core-apps` container expects `../busibox-frontend` to exist on the host, but the installer never clones it
issue: 010
status: open
severity: high
area: install, core-apps
---

# `core-apps` silently waits for a sibling repo that was never cloned

## Symptom

`dev-core-apps` enters a restart loop. Container logs:

```
=================================================================
ERROR: busibox-frontend monorepo is missing or incomplete.

  Expected: /srv/busibox-frontend/package.json
  Expected: /srv/busibox-frontend/pnpm-workspace.yaml

  Contents of /srv/busibox-frontend:
  total 16
  drwxr-xr-x 4 root root 4096 ...
  drwxr-xr-x 9 root root 4096 apps
  drwxr-xr-x 2 root root 4096 node_modules

  This container uses local-dev mode which volume-mounts the
  busibox-frontend repo from the host. The host directory is
  either empty or not a valid monorepo.

  Fix: ensure busibox-frontend is cloned on the host, then
  re-run the installer.
=================================================================
```

Meanwhile on the host, `/home/gabe/maigent-code/busibox-frontend/` exists but only contains `apps/` and `node_modules/` — both created by the container as root, because docker auto-creates missing bind-mount source directories when the container starts. The container's own volume setup produced a partial directory that doesn't look like a monorepo.

This also leaves **root-owned** directories on the host (`/home/gabe/maigent-code/busibox-frontend`, `busibox-frontend.docker-created.bak`) that a regular user can't `rm -rf` without sudo.

## Root cause

`docker-compose.local-dev.yml:154`:

```yaml
volumes:
  - ${BUSIBOX_FRONTEND_DIR:-../busibox-frontend}:/srv/busibox-frontend
```

Combined with several named-volume mounts at `/srv/busibox-frontend/node_modules`, `/srv/busibox-frontend/apps/<app>/node_modules`, and `/srv/busibox-frontend/apps/<app>/.next`. When docker starts the container:

1. It needs a bind-mount source at `../busibox-frontend` (i.e. `$BUSIBOX_DIR/../busibox-frontend`). If missing, docker creates it (as root) so the mount succeeds.
2. It also creates all the named-volume mountpoints under `/srv/busibox-frontend/…`, which **materialize** the `apps/` and `node_modules/` directory structure on the host (because bind-mount child paths are visible through the bind).

The net effect: even a fresh install produces a "busibox-frontend-shaped" directory owned by root, with no actual monorepo content, making the container's own startup check fail.

Nothing in `install_ubuntu_24.sh`, `busibox-quick`, or the ansible playbook clones the `busibox-frontend` repo. The `INSTALL_UBUNTU_24.04.md` doc doesn't mention it either.

## Workaround

```bash
# stop core-apps so the mount releases
docker rm -f dev-core-apps

# delete the root-owned junk directory (needs sudo OR a docker shim)
docker run --rm -v /home/gabe/maigent-code:/w alpine \
  rm -rf /w/busibox-frontend

# clone the real thing
cd ~/maigent-code
git clone https://github.com/jazzmind/busibox-frontend.git

# re-run the installer — core-apps will pick up the proper monorepo
```

## Proposed fix

Three independent improvements:

1. **The installer should clone `busibox-frontend` automatically.** `install_ubuntu_24.sh` already clones / assumes the busibox repo — it should also clone `busibox-frontend` (or error out with a clear message and `git clone` recipe if missing). The Ansible `app_deployer` role that manages `core-apps` is a natural home — check for the repo, clone if absent, checkout the right ref, before the compose up.
2. **Docker should not be auto-creating the bind-mount source.** Fix the compose / setup so missing `busibox-frontend` is a hard error, not a silently-create-an-empty-root-owned-dir. Options:
   - Use a docker bind mount with `type: bind, bind.create_host_path: false` (compose spec supports this in long-form mount syntax; short form doesn't).
   - Or: have the install script `test -f ../busibox-frontend/package.json` up-front and refuse to start core-apps without it.
3. **Doc the dependency.** `INSTALL_UBUNTU_24.04.md` under "Step X" should say: "Before step N, you also need the companion `busibox-frontend` repo cloned as a sibling: `cd ~/maigent-code && git clone <url>`". Mention the expected path (`../busibox-frontend` relative to the busibox repo).

## References

- `docker-compose.local-dev.yml:154` — bind mount with default `../busibox-frontend`
- `provision/docker/core-apps-entrypoint.sh` (or similar) — the preflight check that emits the error message above
- `INSTALL_UBUNTU_24.04.md` — no mention of the companion repo
