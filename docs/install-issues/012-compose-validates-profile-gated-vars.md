---
title: compose file uses `${VLLM_MODEL:?…}` on a profile-gated service, breaking every install that doesn't deploy vllm
issue: 012
status: open
severity: high
area: docker-compose, install
---

# `${VLLM_MODEL:?…}` on a `profiles: [vllm]` service breaks non-vllm installs

## Symptom

Fresh `busibox-quick` install on a machine without a pre-existing model config. Every `docker compose …` the installer runs — postgres up, authz-api build, config-api build, core-apps build, etc. — fails before it starts with:

```
error while interpolating services.vllm.environment.VLLM_MODEL:
required variable VLLM_MODEL is missing a value:
VLLM_MODEL must be set; run scripts/llm/generate-model-config.sh
```

And the installer marches on deploying "proxy" (the only service whose ansible play doesn't invoke compose build/up), reports "Installation complete!" at the end, and leaves the user with zero running services.

## Root cause

`docker-compose.yml:1306` declares the `vllm` service under `profiles: ["vllm"]` — meaning it's only started when `--profile vllm` is passed. `docker compose ps` won't show it, `up` won't start it. Good.

But line 1315:

```yaml
VLLM_MODEL: ${VLLM_MODEL:?VLLM_MODEL must be set; run scripts/llm/generate-model-config.sh}
```

The `:?` form says "abort interpolation if this variable is empty." **Compose evaluates variable interpolation across the entire compose file at parse time**, regardless of profiles. So every `docker compose config`, `build`, `up`, and `pull` — even for completely unrelated services — trips on this check when `$VLLM_MODEL` isn't set.

Running `scripts/llm/generate-model-config.sh` (as the error message suggests) produces `provision/ansible/group_vars/all/model_config.yml`, which is consumed by the ansible `vllm_docker` role to inject VLLM_MODEL into the vllm container's env at deploy time. But that role only runs when deploying vllm — and its env injection happens too late to satisfy compose's parse-time check on *other* services' builds. The env var also isn't written anywhere `docker compose`/`.env.dev` can pick it up, so a user who runs the script still can't run compose.

## Workaround

Add `VLLM_MODEL` (and its friends) to `.env.dev`:

```bash
cat >> .env.dev <<'EOF'
VLLM_MODEL=qwen3.5-0.8b
VLLM_PORT=8000
VLLM_HOST_PORT=8080
EOF
```

Value doesn't matter — the vllm service is profile-gated so it's never started; the var just needs to be non-empty so compose's interpolation pass can proceed.

## Proposed fix

Pick one of:

1. **Use `:-` (default) instead of `:?` (required).** The comment at line 1311-1314 already says "VLLM_MODEL is required and must be set by the installer … the entrypoint will fail fast with a clear error." That second half is enough — the entrypoint's own validation will catch a missing value when vllm is actually being deployed, and a simple `${VLLM_MODEL:-}` default lets the rest of the compose file parse cleanly.
2. **Move vllm to a separate compose file.** `docker-compose.llm.yml` or similar, included only when the operator wants GPU inference. Keeps the main compose file free of GPU-specific env requirements.
3. **Have `scripts/llm/generate-model-config.sh` also write `VLLM_MODEL=…` into `.env.dev`** (or a dedicated `.env.llm` file included via `env_file:` on the vllm service). Then fresh installs that run the script as part of install get the variable populated without touching compose file semantics.

Option 1 is the smallest change and aligns with the stated intent in the compose file comment. Recommend starting there.

## References

- `docker-compose.yml:1306-1355` — vllm service definition with `profiles: [vllm]` and `:?` on VLLM_MODEL
- `scripts/llm/generate-model-config.sh` — produces `model_config.yml` but not a compose-consumable env file
- `provision/ansible/roles/vllm_docker/tasks/docker.yml:133` — where ansible injects VLLM_MODEL at deploy-time for the vllm service only
- Compose profiles docs: https://docs.docker.com/compose/how-tos/profiles/ — profiles don't skip interpolation
