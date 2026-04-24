---
title: SMTP vault placeholders are left as `CHANGE_ME_*` by the workaround in issue #008, so login magic-links are silently redirected to `bridge-api` logs instead of email — and nothing tells the operator this
issue: 015
status: workaround-applied
severity: medium
area: cli, vault, docs, operator-experience
---

# SMTP placeholders block login; the code is dumped to `bridge-api` logs with no user-facing hint

## Symptom

After a successful install (all 19 containers healthy, `https://localhost/portal` loads, etc.), the operator enters their admin email on the Portal login page. The UI says something like "check your email for a sign-in code." No email ever arrives. The operator waits, retries, concludes the install is broken.

Meanwhile, in `bridge-api`'s logs:

```
2026-04-23 22:52:10,951 - app.email_client - WARNING -
[EMAIL] No provider configured — email NOT sent to gabe.spradlin@gmail.com:
Sign in to Busibox Portal - Your code: 633807
```

The 6-digit code is right there — but in a log file inside a docker container, which nobody told the operator to look at.

## Root cause

Two independent issues that compound:

1. **Issue #008's workaround deliberately leaves SMTP settings as `CHANGE_ME_*`.** The sed-replace that fixes the required-for-boot placeholders (postgres, minio, authz, jwt, litellm, etc.) intentionally skips `CHANGE_ME_SSL_EMAIL@example.com`, `CHANGE_ME_SMTP_HOST`, `CHANGE_ME_SMTP_USER`, `CHANGE_ME_SMTP_PASSWORD`, `CHANGE_ME_FROM_EMAIL`, because they're not needed for containers to start. Fine for a smoke test, but no email transport works until they're filled in.

2. **`bridge-api`'s email client has a silent-log fallback.** When no SMTP provider is configured, `app/email_client.py` logs the message body as a warning and returns success to the caller. `authz-api` sees `POST /api/v1/email/send-magic-link → 200 OK` and happily tells the Portal "email sent," even though nothing went over the wire.

The combined effect: install appears to succeed, login appears to succeed, but the user is locked out of their own portal with no indication of why — unless they read `bridge-api` logs, which are mentioned nowhere in `INSTALL_UBUNTU_24.04.md` or the Portal's login UI.

## Workaround

Grab the code from the log immediately after triggering a login:

```bash
docker logs --tail 50 dev-bridge-api 2>&1 | grep -i "code:" | tail -1
```

Enter that code on the Portal page. Codes expire in a few minutes; re-trigger a login if stale.

If you want actual email delivery, update the vault with real SMTP creds:

```bash
cd ~/maigent-code/busibox
export ANSIBLE_VAULT_PASSWORD="$(echo -n <master> | \
  ./cli/target/release/examples/print-vault-password local-development-docker)"
VAULT=provision/ansible/roles/secrets/vars/vault.local-development-docker.yml
ansible-vault edit "$VAULT" --vault-password-file scripts/lib/vault-pass-from-env.sh
# …edit the smtp_* / from_email / ssl_email entries…
# then redeploy bridge-api so it picks up the new secrets:
#   cd provision/ansible
#   ANSIBLE_VAULT_PASSWORD=... ansible-playbook -i inventory/docker docker.yml \
#     -e container_prefix=dev -e vault_prefix=local-development-docker \
#     --vault-password-file ../../scripts/lib/vault-pass-from-env.sh \
#     --tags bridge
```

## Proposed fix

Three complementary improvements:

1. **Make the "no provider configured" path visible to the user.** `bridge-api`'s response to `/api/v1/email/send-magic-link` should include a `delivery_mode: "log_only"` flag when email is suppressed, and `authz-api` / Portal should surface that in the UI ("Email isn't configured on this host — check `docker logs dev-bridge-api` for your code"). Today everything signals success.

2. **`busibox-quick` should offer a dev-mode choice at setup time.** Either (a) configure SMTP (prompt for host/user/pass, write to vault), or (b) accept "I'll grab codes from the log" — and in case (b) print a one-liner to `~/.busibox/notes/local-dev-login.md` explaining the recipe above, so it's recoverable without tribal knowledge.

3. **Document the fallback in `INSTALL_UBUNTU_24.04.md`.** Add a "First Login (Local Dev)" section that says: "You'll be asked for a 6-digit code from email. On a local-dev install without SMTP, grab the code from bridge-api logs: `docker logs --tail 50 dev-bridge-api | grep 'code:'`. To get real email delivery, see docs/.../smtp-setup.md."

## References

- `bridge-api` container, `app/email_client.py` — the silent-log fallback
- Issue #008 — companion workaround that leaves these specific placeholders in place
- `provision/ansible/roles/secrets/vars/vault.example.yml` — the template with `smtp_*` / `ssl_email` / `from_email` CHANGE_ME values
