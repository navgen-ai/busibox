---
title: pg_hba.conf uses trust for local but scram-sha-256 for TCP, hiding password drift
issue: 003
status: open
severity: medium
area: postgres, provisioning
---

# `pg_hba.conf` uses `trust` locally but `scram-sha-256` over TCP, hiding password drift

## Symptom

Services running on the docker network fail to authenticate:

```
FATAL: password authentication failed for user "busibox_user"
```

But debugging from the `dev-postgres` container `psql -U busibox_user -d data` (Unix socket or 127.0.0.1) works fine — making the issue very hard to diagnose. You can burn 30 minutes convinced the password is right because "psql works."

## Root cause

The postgres container's `pg_hba.conf` on this install contained:

```
local   all             all                                     trust
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 trust
host    all             all             all                     scram-sha-256
```

The `trust` rules mean any password (including wrong ones) succeed for local connections. Password auth is only enforced on non-loopback TCP — exactly the path every container-to-container connection takes.

In our case the `busibox_user` role in Postgres had a SCRAM hash from some earlier provisioning run that did **not** match the `devpassword` value in `.env`. Debugging from `psql -h localhost` looked fine; the apps on the docker network got auth failures.

## Workaround

```sql
ALTER USER busibox_user WITH PASSWORD 'devpassword';
ALTER USER busibox_test_user WITH PASSWORD 'testpassword';
```

Data intact, no volume wipe needed.

## Proposed fix

Two independent improvements:

1. **Make debugging easier.** The ansible role that owns postgres (`provision/ansible/roles/postgres`) should either:
   - Use `scram-sha-256` for loopback too (matches network behavior; if the password doesn't work, it doesn't work from anywhere), **or**
   - Print a prominent warning in the role output: "pg_hba is trust-locally, scram-over-TCP — if your apps can't authenticate but `psql` works, you have password drift."
2. **Make drift impossible.** The role currently sets the password on initial DB creation via `POSTGRES_PASSWORD` env var, but doesn't re-sync it on subsequent runs. Add an `ALTER USER` step that runs every deploy to assert the password matches what's in the vault / `.env`. This is idempotent and cheap.

## References

- Observed on a `postgres:16-alpine` container that survived a partial re-provisioning.
- Docker compose file references `${POSTGRES_PASSWORD}` but doesn't verify it's actually stored in the DB.
