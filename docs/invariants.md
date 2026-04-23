# Busibox — Invariants

Foundational properties that must hold at all times. A violation is a bug regardless of features delivered.

Invariants are extracted from the current codebase and documentation (README.md, CLAUDE.md, `.cursor/rules/`, `docs/developers/architecture/`, `docs/developers/reference/`). Items marked **⚠ UNDOCUMENTED** are implied but not stated explicitly anywhere — those are the first gaps to close.

---

## Actors

Every action in the system is attributable to one of these principals:

- **End users** — humans authenticated via Portal (passkey / TOTP / magic link / SSO)
- **Admin / operator** — humans managing installation (CLI, Portal admin UI, SSH)
- **Service identity** — an API service (authz, data, agent, search, docs, embedding, deploy) identified by an audience-scoped RS256 JWT
- **Agent** — an LLM-backed actor running inside the Agent service; always acts under a specific human user's delegated identity
- **User app** — a Next.js app deployed via the Deploy API; authenticates via `SessionProvider` and inherits the calling user's identity
- **Bridge channel** — external messaging integrations (Signal / Telegram / Discord / WhatsApp / email); relay messages from/to end users
- **Ingest worker** — background process in the Data service processing the Redis job queue

There are **no anonymous actors.** Every request is attributable to one of the above before it reaches business logic.

---

## INV-1 — Single-tenant, multi-user isolation

Each Busibox installation serves exactly one organization. Within an installation, users see only what their RBAC role membership permits. There is no cross-installation leakage, and there is no shared data across users unless explicitly shared via a role.

**Source:** README.md "Security Model" section, `docs/developers/architecture/11-document-sharing.md`

---

## INV-2 — Zero Trust Authentication

All inter-service communication uses audience-scoped RS256 JWTs, verified against the AuthZ service's JWKS endpoint. There are **no static service-to-service API keys, no shared secrets, and no long-lived client credentials** for user operations.

- AuthZ is the sole token authority
- Token exchange is the only way to get a service-specific token
- Token exchange scopes come from the RBAC database, **never from the incoming token**
- Every issued token is bound to a single audience (target service)
- JWTs are signed with RS256; verifiers use JWKS, never a shared secret

**Source:** `.cursor/rules/003-zero-trust-authentication.md`, `docs/developers/architecture/03-authentication.md`

---

## INV-3 — Passwordless authentication by design

Busibox does not accept passwords as a primary credential. Users authenticate via: passkey (WebAuthn/FIDO2), TOTP, magic link (email), or federated SSO (EntraID, SAML). Passwords exist only as a fallback for specific SSO flows and are never stored as the primary auth factor.

**Source:** README.md

---

## INV-4 — Database-enforced row-level security

PostgreSQL Row-Level Security (RLS) policies enforce data access at the database layer. Even if an application-layer bug omits a permission check, the database will not return rows the caller is not authorized to see.

- Every user-scoped table has an RLS policy
- Services set `app.user_id` and `app.user_role_ids_*` session variables on each request from the verified JWT
- RLS policies filter by these session variables
- No application code may bypass RLS (no raw `SET row_security = off`)
- Migrations that disable RLS for bulk operations must re-enable it before commit

**Source:** README.md, `docs/developers/architecture/11-document-sharing.md`

---

## INV-5 — RBAC assignment is the sole authorization mechanism

Documents, agents, apps, and tools are assigned to roles. Users are assigned to roles. A user can perform an action if and only if their role membership permits it. There is no per-user ACL — only role-based authorization.

Three role categories:
- **App roles** (`app:<name>`) — access to an app
- **Team/entity roles** (`app:<name>:<entity>`) — scoped to a row/entity
- **User roles** — global personal scope

**Source:** README.md, `docs/developers/architecture/11-document-sharing.md`

---

## INV-6 — Documents have exactly three visibility modes

Every document has one of: `personal` (owner only), `shared` (visible to assigned roles), or `authenticated` (any authenticated user in this installation). There is no fourth mode, no public/unauthenticated document access, and no share-by-link.

**Source:** `docs/developers/architecture/11-document-sharing.md`, README.md

---

## INV-7 — Agents inherit the caller's identity and permissions

An agent cannot access data the calling user could not access directly. Every tool call an agent makes carries the user's identity via token exchange. Agents cannot escalate privileges, cannot impersonate other users, and cannot cache data across users.

**Source:** README.md "Security Model", `docs/developers/architecture/06-agents.md`

---

## INV-8 — Files are encrypted at rest with envelope encryption

File contents in MinIO are encrypted with a per-file Data Encryption Key (DEK). DEKs are encrypted by a per-namespace Key Encryption Key (KEK), and KEKs are encrypted by a Master Key. The Master Key is stored in Ansible Vault and never appears on disk in plaintext on any service host.

**Source:** README.md "Security Model"

---

## INV-9 — Non-destructive database migrations

Schema changes never discard user data. `prisma db push --accept-data-loss` is forbidden. Destructive migrations (dropping columns, renaming tables) require a pre-migration script in `prisma/pre-migrations/` that runs before the schema push.

**Source:** `.cursor/rules/005-database-practices.md`

---

## INV-10 — Model registry is the single source of truth for embeddings

`model_registry.yml` declares every embedding model's dimension, truncation options, and config. No service may hard-code an embedding dimension. A dimension mismatch between ingest and search must be caught at config-load time, never at query time.

**Source:** `.cursor/rules/004-embedding-configuration.md`

---

## INV-11 — Ingestion is a checkpointed pipeline

Document ingestion is a multi-stage pipeline (upload → store → chunk → embed → index). State is persisted after each stage in PostgreSQL `data.files.status`. A worker crash mid-pipeline must be able to resume from the last checkpoint, not restart the entire file.

For PDFs specifically, the 3-pass progressive pipeline (fast-extract → OCR → LLM cleanup) means the document is viewable after Pass 1 even if Passes 2-3 have not yet completed or fail.

**⚠ UNDOCUMENTED:** The exact resume semantics on worker crash (which stages are idempotent, which require rollback) are not documented anywhere in `docs/`. This is one of the most load-bearing invariants in the system and deserves an explicit spec.

**Source:** `docs/developers/architecture/04-ingestion.md` (implies but doesn't guarantee)

---

## INV-12 — Audit trail is append-only and complete

Every authentication event, token exchange, admin action, and mutating operation on a user resource is logged with: timestamp, actor identity, source IP, target resource, and action. The audit log is append-only — entries are never modified or deleted.

**Source:** README.md "Security Model"

**⚠ UNDOCUMENTED:** The exact schema of the audit log, its retention policy, and which services write to it (does every service write independently, or is there a central audit API?) are not documented.

---

## INV-13 — Container isolation is a security boundary

Each service runs in its own container (Docker or LXC). Compromise of one service's container does not grant access to another service's data or secrets. Inter-service communication uses audience-scoped JWTs (INV-2), never shared filesystem or shared memory.

**Source:** README.md "Architecture" and "Security Model"

---

## INV-14 — Agent resource guardrails are enforced, not advisory

Every agent invocation has a hard ceiling on: request count, total tokens, cumulative cost, and wall-clock time. Crossing the ceiling terminates the agent invocation. Guardrails are enforced inside the Agent service, not trusted to the LLM or the caller.

**Source:** README.md "AI Agents with Guardrails"

**⚠ UNDOCUMENTED:** The exact enforcement scope (per-request / per-conversation / per-user / per-day), the default limits, and who can override them are not documented. This is a critical safety invariant with undefined mechanics.

---

## INV-15 — Delegation tokens are the only way for background jobs to act as a user

A user can issue a delegation token with a narrow scope (e.g., `ingest:read`) and explicit expiration. Background jobs use this token as a `subject_token` in token exchange to get short-lived, service-scoped tokens. A revoked delegation token ceases to work immediately across all services.

**Source:** `docs/developers/architecture/03-authentication.md`

---

## INV-16 — Secrets never appear on a remote host in plaintext

The Ansible Vault password is encrypted at rest on the admin workstation (AES-256-GCM, Argon2id-derived key). During deployment, the vault password is piped to the remote host via SSH stdin and never written to disk on the remote. Service secrets (DB passwords, signing keys) are injected at service startup via Ansible Vault, never via environment files committed to disk.

**Source:** `docs/developers/reference/mcp-and-make-internals.md`, CLAUDE.md "Vault Password Architecture"

---

## INV-17 — Deployments are through the Busibox CLI / MCP / Deploy API only

Direct `docker compose`, `docker`, or `ansible-playbook` invocation from a human is forbidden. All deployments go through one of: the Busibox CLI (interactive), an MCP server (AI agents), or the Deploy API (programmatic). This enforces vault-password injection, environment detection, and audit logging.

**Source:** CLAUDE.md "CRITICAL: Service Operations"

---

## INV-18 — App installation is runtime, not build-time

User apps and core apps (Portal, Agents) are cloned and built inside the `core-apps` / `user-apps` container at deploy time. They are never baked into Docker images. This means app updates never require a container rebuild and a deployment failure cannot brick a container.

**Source:** CLAUDE.md "Deployment Architecture"

---

## INV-19 — Hybrid LLM routing is per-agent, per-task

No agent is hard-wired to a specific LLM model. All LLM calls go through LiteLLM, which routes based on configuration. The choice of local (vLLM/MLX/Ollama) vs cloud (OpenAI/Anthropic/Bedrock) is a configuration concern, not a code concern.

**Source:** README.md, `docs/developers/architecture/02-ai.md`

---

## INV-20 — Partition-scoped Milvus queries match user role membership

Milvus vector searches are always scoped to the union of partitions the user is entitled to (personal partition + each role partition). A search that would return a chunk from a partition the user has no access to is a critical bug.

**Source:** `docs/developers/architecture/05-search.md`

**⚠ UNDOCUMENTED:** Exact Milvus partition cleanup on document delete, role revoke, or user deletion is not documented.

---

## Gaps to investigate (not yet invariants)

These are behaviors the system likely needs but that aren't stated anywhere:

1. **Session TTL and refresh flow** — Session JWT lifetime, refresh token mechanism, behavior when a session expires mid-chat-stream
2. **Rate limiting** — Per-user request ceilings on Data API, Search API, Agent API; what is the response when exceeded
3. **Ingest worker crash recovery** — Exact resume behavior after crash during each ingestion stage (see INV-11)
4. **LLM failover** — Whether LiteLLM gateway retries on primary-model failure or surfaces the error
5. **Secret rotation** — Zero-downtime rotation procedure for JWT signing keys, DB passwords, Master Key
6. **Search result caching** — Whether hybrid search results are cached, and cache invalidation rules
7. **Bridge webhook retries** — Retry policy and dead-letter behavior for bridge channel delivery failures
8. **Audit log retention** — How long audit entries are kept; where they go on expiration
9. **Cross-service clock skew tolerance** — JWT validation `nbf`/`exp` skew allowance between services
10. **What a fresh install looks like when AuthZ is unreachable** — graceful degradation or hard failure

---

## Maintenance

When adding a new invariant:
1. Give it the next INV-N number (do not renumber existing entries)
2. State the invariant in one sentence; expand below
3. Cite the source file(s) where it is established
4. If it is implied but not explicit, mark **⚠ UNDOCUMENTED**

Invariants are never deleted — if an invariant no longer holds, mark it `DEPRECATED (YYYY-MM-DD): <reason>` and link to the replacement.
