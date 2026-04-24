---
title: Install flow blocks automation — `busibox-quick` and the TUI read passwords from /dev/tty, not stdin
issue: 006
status: open
severity: medium
area: cli, install-automation
---

# Install flow blocks automation — passwords read from /dev/tty

## Symptom

An automated install / configuration management agent (Claude Code, Ansible running ad-hoc, CI driver, etc.) cannot drive `busibox-quick` or the full `busibox` TUI because both tools read the master password via `rpassword::prompt_password`, which opens `/dev/tty` directly. Piping the password in via stdin has no effect. There's also no `--admin-email` or `--master-password` flag, and no config-file mode.

## Root cause

`cli/busibox-quick/src/main.rs:74,80` — master password and confirmation are read with `rpassword::prompt_password`, which is a deliberately-terminal-only input path (so the password doesn't leak into shell history or stdin logs). The admin email on line 50 *is* read from stdin, so that part is scriptable, but the password prompts are not.

The full `busibox` TUI is even less scriptable — it's a ratatui-based interactive interface.

## Workaround

Two options:

1. **Human runs `busibox-quick` in the current shell.** In Claude Code specifically, prefix the command with `!` so the user enters it interactively: `! ./cli/target/release/busibox-quick`.
2. **Bypass the CLI entirely** and drive `make install SERVICE=…` directly with `ANSIBLE_VAULT_PASSWORD` exported. This is what the CLI does under the hood, just without the setup wizard.

Neither is great: option 1 requires a human at the terminal for what is otherwise an automatable sequence, and option 2 violates the project's own guidance in `CLAUDE.md` ("NEVER run `docker compose`, `docker`, or `ansible-playbook` commands directly").

## Proposed fix

Add a non-interactive install mode to `busibox-quick`. Suggested CLI surface:

```bash
busibox-quick \
  --admin-email alice@example.com \
  --master-password-file /run/secrets/busibox-master \
  --yes
```

with `--master-password-stdin` as an alternative to the file form. Make the admin email + master password the only required inputs; everything else is auto-derived from hardware detection (as today).

For the full TUI, add a `busibox install --profile ... --admin-email ... --master-password-file ...` non-interactive subcommand that replays what the TUI's install wizard does.

The security goal (don't leak passwords to shell history) is already addressed by the file/stdin forms — `rpassword` isn't the only way to protect secret input.

## References

- `cli/busibox-quick/src/main.rs:50` — admin email prompt (stdin, OK)
- `cli/busibox-quick/src/main.rs:74,80` — master password prompts (tty, blocks automation)
- `CLAUDE.md` — "Prefer the `mcp-admin` MCP server tools when available" — but that server itself probably hits the same problem if the profile isn't already set up.
