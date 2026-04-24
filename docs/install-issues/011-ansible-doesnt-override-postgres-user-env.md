---
title: docker_common ansible role sets POSTGRES_PASSWORD in the compose subprocess env, but not POSTGRES_USER — shell POSTGRES_USER still leaks through
issue: 011
status: open
severity: medium
area: ansible
---

# docker_common passes `POSTGRES_PASSWORD` but forgets `POSTGRES_USER` — shell env leaks in

## Symptom

Even after [issue #001 workaround](001-bashrc-postgres-env-sabotages-compose.md) (unsetting `POSTGRES_PASSWORD` before running make), services still come up with the wrong `POSTGRES_USER`. Container env:

```
POSTGRES_USER=admin        # from operator's shell
POSTGRES_PASSWORD=devpassword   # correctly from ansible/vault
```

…because ansible explicitly sets `POSTGRES_PASSWORD` in the subprocess env for its `docker compose up` invocation, overriding shell env at that point — but `POSTGRES_USER` is never set by ansible, so shell env still wins when compose interpolates `${POSTGRES_USER:-busibox_user}`.

The consequence is the same as issue #001: authz-api (and every other service whose compose block reads POSTGRES_USER) tries to connect as `admin` with a password that doesn't exist for that role. Auth fails.

## Root cause

`provision/ansible/roles/docker_common/tasks/compose_up.yml:37-67` defines `&docker_secrets_env`:

```yaml
environment: &docker_secrets_env
  …
  POSTGRES_PASSWORD: "{{ busibox_secrets.POSTGRES_PASSWORD }}"
  MINIO_ROOT_USER: "{{ busibox_secrets.MINIO_ROOT_USER }}"
  MINIO_ROOT_PASSWORD: "{{ busibox_secrets.MINIO_ROOT_PASSWORD }}"
  …
```

Notice `POSTGRES_PASSWORD` is set, but `POSTGRES_USER` is not. `shared_secrets.yml` does provide it:

```yaml
POSTGRES_USER: "{{ postgres_user | default('busibox_user') }}"
```

…so `busibox_secrets.POSTGRES_USER` is available — it just isn't passed to the compose subprocess. Shell env wins, compose interpolates the shell value.

Confirmed by unsetting shell env before invocation:

```bash
env -u POSTGRES_USER -u POSTGRES_PASSWORD ansible-playbook …
# containers now get POSTGRES_USER=busibox_user as expected
```

## Workaround

For every `ansible-playbook`, `make install`, or `docker compose` invocation, prepend:

```bash
env -u POSTGRES_USER -u POSTGRES_PASSWORD …
```

Or remove the exports from `~/.bashrc` (issue #001's long-term fix).

## Proposed fix

Add `POSTGRES_USER: "{{ busibox_secrets.POSTGRES_USER }}"` to the `&docker_secrets_env` anchor in `compose_up.yml` (and likewise in `compose_build.yml` and any other task that defines an explicit `environment:` for the compose subprocess). The same applies to any other key where shell env could leak:

- `POSTGRES_DB` — less risky (compose mostly hardcodes it per-service) but still an asymmetry
- `NEO4J_USER`, `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` variants — already mostly covered but worth auditing

A more durable solution is to run the `docker compose` subprocess under `env -i` with an explicit allowlist of env vars. Today's setup allows arbitrary shell vars to influence the container environment, which is the root cause of both issue #001 and this one.

## References

- `provision/ansible/roles/docker_common/tasks/compose_up.yml:37` — the `environment:` block that's missing POSTGRES_USER
- `provision/ansible/roles/secrets/vars/shared_secrets.yml:24` — POSTGRES_USER is already in the secrets mapping
- Issue #001 — upstream cause (user's `~/.bashrc` exports)
