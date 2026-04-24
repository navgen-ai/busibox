---
title: The ansible task that should write `.env.dev` from `docker.env.j2` never actually runs — compose gets an empty env file
issue: 009
status: open
severity: high
area: ansible, docker-compose
---

# `.env.dev` template task never runs; compose gets an empty env file

## Symptom

After a full `busibox-quick` install:

```bash
$ wc -c ~/maigent-code/busibox/.env.dev
1 /home/gabe/maigent-code/busibox/.env.dev    # a single newline
$ stat ~/maigent-code/busibox/.env.dev
  Modify: 2026-03-27 16:52:37   # unchanged since weeks before the install
```

Ansible's play log shows the task apparently running:

```
TASK [Generate environment file] ***********************************************
included: docker_common for localhost
```

…but `PLAY RECAP` reports `changed=0`, and `.env.dev` is never written. Every subsequent `docker compose --env-file .env.dev …` runs with no variable substitutions, so any var that isn't set in the shell or inline in the compose file falls back to its default or errors out.

## Root cause

`provision/ansible/docker.yml:165-170`:

```yaml
- name: Generate environment file
  ansible.builtin.include_role:
    name: docker_common
    tasks_from: generate_env
  vars:
    docker_env_template: docker.env.j2
  tags: always
```

The parent `include_role` task has `tags: always`. The child tasks inside `roles/docker_common/tasks/generate_env.yml` have **no tags**. Ansible's behavior for `include_role` (dynamic include) is that child tasks are evaluated at runtime and the parent's tag filter does **not** propagate to them. So when the play is invoked with a tag filter that doesn't match the children (e.g. `--tags always,postgres`), the children are filtered out even though the parent runs.

The fix that was tried first — running without any `--tags` filter — didn't help either, because the child `template` task has `when: docker_env_template is defined`, and when include_role is invoked via the parent task, the `vars:` block may not propagate into the included tasks with the semantics the author expected. The net effect observed: `.env.dev` is never written by any code path.

There's a second contributing factor: even if the template *did* run, the template itself (`docker.env.j2`) is missing keys the compose file now requires — notably `VLLM_MODEL` (see issue #008 / compose validation). So fixing the include_role bug alone wouldn't be enough.

## Workaround

Write `.env.dev` manually, copying from `.env` (which `env.local.example` generates) and appending the missing compose-required keys:

```bash
cp .env .env.dev
cat >> .env.dev <<'EOF'
VLLM_MODEL=qwen3.5-0.8b
VLLM_PORT=8000
VLLM_HOST_PORT=8080
AUTHZ_MASTER_KEY=dev-authz-master-key-32-bytes-long!!
SSO_JWT_SECRET=dev-sso-jwt-secret-32-bytes-long-local
CONFIG_ENCRYPTION_KEY=dev-config-encryption-key-32-bytes-long!
CONTAINER_PREFIX=dev
COMPOSE_PROJECT_NAME=dev-busibox
EOF
```

Because the ansible task silently doesn't run, the hand-rolled `.env.dev` is never overwritten.

## Proposed fix

1. **Change `include_role` → `import_role`** for the "Generate environment file" call in `docker.yml`. `import_role` is static, so child tasks do inherit parent tags correctly. Or add `tags: always` to each task inside `generate_env.yml`.
2. **Make the template complete.** Add `VLLM_MODEL`, `VLLM_PORT`, `VLLM_HOST_PORT` to `roles/docker_common/templates/docker.env.j2`, sourced from `model_config.yml` (the file produced by `scripts/llm/generate-model-config.sh`). Right now the compose file requires these but the template never emits them.
3. **Add an assertion after generation.** After `generate_env.yml` runs, `ansible.builtin.stat` the file and `fail` if its size is < N bytes. That would have caught this weeks ago.
4. **Compose should use `:-` defaults, not `:?` requireds, for profile-gated services.** `VLLM_MODEL: ${VLLM_MODEL:?…}` on a service with `profiles: [vllm]` means every install that doesn't use vllm still has to satisfy the check. Change to `${VLLM_MODEL:-}` and let the vllm entrypoint do its own validation (the comment at line 1311 says that's already the plan — the `:?` is just stricter than intended).

## References

- `provision/ansible/docker.yml:165-170` — the include_role invocation
- `provision/ansible/roles/docker_common/tasks/generate_env.yml` — the child template task (no tags)
- `provision/ansible/roles/docker_common/templates/docker.env.j2` — template that's missing VLLM_MODEL
- `docker-compose.yml:1315` — `VLLM_MODEL: ${VLLM_MODEL:?…}` on a profile-gated service
- Ansible docs on `include_role` vs `import_role` tag inheritance.
