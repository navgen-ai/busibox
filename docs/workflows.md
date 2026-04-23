# Busibox — Workflows

Concrete end-to-end flows the system must support. Each workflow has numbered steps (what happens) and lettered observables (how to verify each step actually occurred).

When evaluating a change, every affected workflow should be walked end-to-end. A workflow that no longer produces its observables is broken, even if every unit test passes.

Workflows are extracted from current architecture docs. Items marked **⚠ UNDOCUMENTED** are implied by the docs but not specified in sufficient detail to verify.

---

## WF-1 — First-time admin install (Proxmox)

The flow an administrator follows to stand up a new Busibox installation on a Proxmox host.

### Steps

1. Admin runs `busibox` CLI on workstation; CLI prompts for profile (production / staging / dev).
2. CLI collects: Proxmox host SSH target, vault master password, network config (subnet, gateway, DNS), LLM model selection based on detected hardware.
3. CLI generates a vault password, encrypts it with the admin master password (Argon2id → AES-256-GCM), writes `~/.busibox/vault-keys/{profile}.enc`.
4. CLI encrypts `provision/ansible/roles/secrets/vars/vault.{prefix}.yml` with the generated vault password; writes secrets (JWT signing key, DB passwords, Master Key).
5. CLI runs `provision/pct/create_lxc_base.sh {profile}` on the Proxmox host via SSH; host creates all LXC containers with assigned CTIDs and IPs.
6. CLI runs `make install SERVICE=all` inside the manager container; manager injects vault password via SSH stdin to each target host.
7. Ansible deploys services in dependency order: infrastructure (Postgres, Redis, MinIO, Milvus) → AuthZ → LiteLLM → APIs (Data, Search, Embedding, Agent, Docs, Deploy) → frontend (nginx, core-apps).
8. CLI runs health checks: `curl` each service's health endpoint; verifies JWKS reachable; verifies AuthZ can issue and exchange tokens.
9. CLI walks the admin through the first-user bootstrap: create first admin user, register their passkey, assign `admin` role, log in to Portal.

### Observables

- **1a.** CLI state file at `~/.busibox/profiles/{profile}/state.json` created.
- **3a.** `~/.busibox/vault-keys/{profile}.enc` exists and decrypts with master password; no plaintext vault password on disk.
- **4a.** `provision/ansible/roles/secrets/vars/vault.{prefix}.yml` is ansible-vault-encrypted (starts with `$ANSIBLE_VAULT;1.1;AES256`).
- **5a.** `pct list` on Proxmox host shows all expected CTIDs (200–219 range) in `running` state.
- **6a.** Manager container image exists; `docker ps` on admin workstation shows the ephemeral manager running during deploy.
- **7a.** Each service container's systemd unit is `active (running)`; `journalctl -u {service}` shows no ERROR-level entries.
- **7b.** Postgres has `busibox_{service}` DBs created with RLS enabled per INV-4.
- **7c.** Milvus has no collections yet (collections are created lazily on first ingest).
- **7d.** LiteLLM config loaded; `litellm --models` lists configured routes.
- **8a.** HTTP 200 from: `https://{domain}/authz/.well-known/jwks.json`, `/data/health`, `/agent/health`, `/search/health`.
- **8b.** Round-trip token exchange succeeds: AuthZ issues a session JWT → AuthZ exchanges it for a data-api-audience JWT → Data API verifies via JWKS.
- **9a.** Postgres `authz.users` has the admin user row; `authz.user_passkeys` has the registered credential; `authz.user_roles` has the admin role assignment.
- **9b.** Portal login succeeds; admin lands on dashboard with admin nav visible.

### Verification script

```bash
# After CLI reports "Install complete":
busibox doctor --profile production  # runs all observable checks above
```

**⚠ UNDOCUMENTED:** `busibox doctor` is not documented as a command; this may need to be implemented, or the check procedure moved to a documented script.

---

## WF-2 — End user uploads a document and searches it

The most common end-user path. Upload → ingest → search.

### Steps

1. User logs into Portal; AuthZ issues a session JWT (RS256, audience=`portal`).
2. User drags a PDF onto Portal upload target; Portal POSTs multipart form to Data API `/files` with the session JWT exchanged for an `audience=data-api` JWT.
3. Data API:
   - a. Verifies JWT, sets `app.user_id` and `app.user_role_ids_*` Postgres session vars.
   - b. Computes SHA-256 of file; if duplicate of existing `data.files` row for the same user, returns existing fileId without re-ingesting.
   - c. Encrypts file with fresh DEK; stores ciphertext in MinIO at `{userId}/{fileId}/{filename}`.
   - d. Inserts `data.files` row with `status=uploaded`, visibility, roles.
   - e. Pushes job `{fileId}` to Redis Stream `jobs:data`.
   - f. Returns `202` with `fileId` and SSE URL `/status/{fileId}`.
4. Data Worker consumes job, dispatches to pipeline by file type:
   - **PDFs:** Pass 1 (`pymupdf4llm` fast extract + layout → updates `status=pass1_done`, document now viewable) → Pass 2 (Tesseract OCR on changed pages → `status=pass2_done`) → Pass 3 (LLM cleanup + selective Marker → `status=pass3_done`).
   - **Non-PDFs:** Single pass (`pdfplumber` / Marker / converter) → `status=extracted`.
5. Worker chunks extracted text (400–800 tokens, ~12% overlap), writes `data.chunks` rows.
6. Worker embeds chunks via FastEmbed (or ColPali for visual chunks) using the model declared in `model_registry.yml`; writes vectors to Milvus partition `user_{userId}` plus any role partitions the file is shared with.
7. Worker writes extracted markdown artifact back to MinIO at `{userId}/{fileId}/extracted.md`.
8. Worker flips `data.files.status=indexed`, publishes `file.indexed` event.
9. Later, user types query into Portal search; Portal calls Search API with `audience=search-api` JWT.
10. Search API:
    - a. Verifies JWT, sets Postgres session vars.
    - b. Computes query embedding via same model as ingest.
    - c. Resolves allowed Milvus partitions from user role membership.
    - d. Runs hybrid search: Milvus vector search (top-K per partition) + BM25 keyword search over `data.chunks.text`.
    - e. Merges results, optionally reranks via LiteLLM (model from config).
    - f. Applies RLS-enforced metadata lookup for each hit.
    - g. Returns ranked results with source citations.

### Observables

- **1a.** `authz.sessions` row with JTI; session JWT stored in HTTP-only cookie.
- **2a.** Portal browser DevTools: `Authorization: Bearer <jwt>` with `aud=data-api` on upload request.
- **3a.** Postgres session vars set for the connection (verify in an ingest log).
- **3c.** MinIO `mc ls busibox/{userId}/{fileId}/` shows encrypted file.
- **3d.** `data.files` row with expected `user_id`, `status=uploaded`.
- **3e.** `XLEN jobs:data` > 0; `XRANGE jobs:data - +` shows the new job entry.
- **4a.** `data.files.status` progresses through `uploaded → pass1_done → pass2_done → pass3_done` (PDF) or `uploaded → extracted` (non-PDF); each transition visible via SSE stream.
- **5a.** `data.chunks` rows exist with expected `file_id`, chunk count matches log output.
- **6a.** Milvus `list_partitions` for the collection shows `user_{userId}` partition; `num_entities` increased by chunk count.
- **7a.** MinIO has `{userId}/{fileId}/extracted.md`.
- **8a.** `data.files.status=indexed`.
- **10d.** Search API log shows Milvus `search` call with expected `partition_names` filter.
- **10g.** Search response includes only chunks the user is entitled to (verify by creating a second user, uploading to them, and confirming user A's search does not surface user B's chunks).

### Verification script

Use `TESTING.md` reference procedure (`make test-docker SERVICE=data` for ingest unit tests + `make test-docker SERVICE=search` for search integration).

**⚠ UNDOCUMENTED:** There is no end-to-end smoke test that walks all the observables in one run. A scripted `busibox smoke` would close a significant gap.

---

## WF-3 — User has a conversation with an agent

The agent chat flow, including tool use, streaming, and guardrail enforcement.

### Steps

1. User opens Agents app (or Portal chat panel); app requests `audience=agent-api` JWT from AuthZ via token exchange.
2. User types prompt, selects toggles (web search, document search, file attach); app POSTs to Agent API `/chat/{conversationId}` with streaming=SSE.
3. Agent API:
   - a. Verifies JWT, sets session vars.
   - b. Loads or creates `agent.conversations` row.
   - c. Initializes guardrail context: remaining request count, token budget, cost ceiling, timeout deadline.
   - d. Classifies prompt or routes to configured sub-agent (RAG / Web / Chat / Attachment).
4. Sub-agent executes:
   - **RAG:** calls Search API with the user's delegated JWT (token exchange); retrieves top-K chunks; constructs prompt with chunks + citations.
   - **Web:** calls external web search; returns snippets.
   - **Attachment:** calls Data API for file content / metadata.
   - **Chat:** skips retrieval; sends prompt directly to LiteLLM.
5. Sub-agent calls LiteLLM with the routed model; streams tokens back to Agent API.
6. Agent API:
   - a. Streams SSE events to the client: `{type: token, delta: "..."}`, `{type: citation, source: "..."}`, `{type: tool_call, name: "..."}`.
   - b. Checks guardrail ceilings after each token; terminates with `{type: guardrail_exceeded, reason: ...}` if any ceiling crossed.
   - c. Appends to `agent.messages` as tokens arrive.
7. On completion, Agent API writes `agent.messages` final row, closes SSE, logs token usage and cost to audit trail.

### Observables

- **1a.** AuthZ audit log shows `token.exchange` event with source audience `portal`/`agents-app` and target audience `agent-api`.
- **3b.** `agent.conversations` row; `agent.conversation_messages` grows as the conversation progresses.
- **3c.** Agent log shows guardrail initialization with configured limits.
- **4a.** (RAG) Search API log shows the retrieval query; returned chunk IDs logged in Agent trace.
- **4a.** (Web) Agent log shows external web search call.
- **5a.** LiteLLM log shows request with the expected model; response streaming.
- **6a.** Client receives SSE frames; Network tab shows `EventStream` with token/citation frames.
- **6b.** (Failure path) Issue a prompt that forces tool-call loops; verify guardrail termination event fires before unbounded loop.
- **7a.** `agent.conversation_messages` has a `completed_at`; audit log has a `token.usage` entry with model, total_tokens, cost.

**⚠ UNDOCUMENTED:** The exact enforcement scope of guardrails (per-request / per-conversation / per-user / per-day) is not specified — see `docs/invariants.md` INV-14.

---

## WF-4 — User deploys a custom app

A developer builds an app from the template and deploys it to their Busibox.

### Steps

1. Developer clones `busibox-template`; creates app with custom domain logic; installs `@jazzmind/busibox-app` library; commits and pushes to a GitHub repo.
2. Developer opens Portal admin UI → Apps → Deploy; enters GitHub repo URL and branch/tag.
3. Portal POSTs to Deploy API `/apps` with repo URL + ref.
4. Deploy API:
   - a. Verifies caller has `admin` or `app_deployer` role.
   - b. Clones repo into user-apps container at `/apps/{app_slug}/` on the configured ref.
   - c. Runs `npm install && npm run build` inside user-apps container.
   - d. Reads `portal.config.yml` from the app; registers the app's metadata in `data.apps` (slug, display name, icon, routes).
   - e. Creates the app's default role (`app:{slug}`) if not present; assigns to the deploying user.
   - f. Writes nginx config snippet routing `/apps/{slug}/*` to the app's port in user-apps; reloads nginx.
5. Deploy API returns status; Portal displays "Deployed" with app's URL.
6. User navigates to `/apps/{slug}`; app's `SessionProvider` exchanges the Portal session JWT for an `audience={slug}` JWT; renders.

### Observables

- **3a.** Deploy API audit entry: `app.deploy_requested` with user and repo.
- **4b.** User-apps container: `ls /apps/{slug}/` shows the cloned repo at the requested ref.
- **4c.** `/apps/{slug}/.next/` exists (for Next.js apps).
- **4d.** `data.apps` row with correct slug; Portal Apps list shows the new app.
- **4e.** `authz.roles` row `app:{slug}`; `authz.user_roles` has the deploying user in that role.
- **4f.** nginx config has a new `location /apps/{slug}/` block; nginx reload succeeded without errors.
- **6a.** Browser can load `/apps/{slug}`; the app sees a session; the user's identity renders in the app's UI.
- **6b.** Token exchange audit entry with target audience `{slug}`.

**⚠ UNDOCUMENTED:** Rollback behavior on deploy failure (partial clone, failed build, failed nginx reload) is not specified. Does the slot remain partially broken, or is it rolled back atomically?

---

## WF-5 — Bridge channel delivers a message from an external channel

A user sends a message via Signal (or Telegram / Discord / WhatsApp / email) that reaches an agent and replies back through the same channel.

### Steps

1. Admin configures bridge channel in Portal → Settings → Bridge; enters channel credentials (e.g., Signal phone number, Telegram bot token).
2. Bridge service (CT 211) polls / listens for inbound messages on the configured channel.
3. External user sends a message; Bridge service receives it.
4. Bridge service:
   - a. Resolves the sender's Busibox user identity by the channel-specific identifier (Signal phone number → `data.bridge_identities`).
   - b. If no mapping exists, either (i) rejects with an unknown-user reply, or (ii) triggers an onboarding flow (configurable per channel).
   - c. Obtains a delegation token for the mapped user via a preconfigured bridge service credential.
   - d. Calls Agent API with the user's delegated JWT and the configured bridge agent.
5. Agent API processes per WF-3; streams response back.
6. Bridge service:
   - a. Collects the full response from Agent API.
   - b. Formats for the channel (message length limits, attachment handling, reactions).
   - c. Sends response via the channel's SDK.

### Observables

- **1a.** `data.bridges` row for the configured channel; credentials stored in Ansible Vault, not in Postgres plaintext.
- **3a.** Bridge service log shows inbound message receipt with channel and external user ID.
- **4a.** `data.bridge_identities` lookup succeeds for known users; audit log records the identity resolution.
- **4c.** AuthZ audit entry: `delegation_token.used` with the bridge service identity as subject_token holder.
- **5a.** Standard WF-3 observables.
- **6c.** Channel SDK log: message sent; channel-specific receipt (Signal delivery ack, Telegram message ID returned).

**⚠ UNDOCUMENTED:**
- The exact unknown-user policy (reject vs onboard) is not specified.
- Retry and dead-letter behavior for inbound or outbound channel failures is not specified.

---

## WF-6 — Recurring document re-embedding on model upgrade

When the admin changes the configured embedding model, existing documents need to be re-embedded at the new model's dimensions. This is the most dangerous recurring flow — done wrong, it breaks all search until complete.

### Steps

1. Admin updates `model_registry.yml`: either changes the active embedding model, or changes truncation dimension for a Matryoshka model.
2. Admin runs `make embedding-reindex SERVICE=data` (or equivalent).
3. Data worker:
   - a. Creates a new Milvus collection at the new dimension, leaving the old collection intact.
   - b. Walks `data.chunks` in batches, re-embeds with the new model, writes to the new collection.
   - c. On each batch completion, updates `data.files.reindex_progress`.
4. Search API is configured to dual-read during the transition: queries both collections, merges results, preferring the new collection when present.
5. When `data.files.reindex_progress = 100%` for all files, admin flips the "active" collection alias atomically in Milvus.
6. Data worker drops the old collection after a grace period.

### Observables

- **1a.** `model_registry.yml` diff shows the new model.
- **3a.** Milvus `list_collections` shows both old and new collections.
- **3c.** `data.files.reindex_progress` monotonically increases; visible via admin UI.
- **4a.** Search API logs show queries against both collections during transition.
- **5a.** Milvus alias switch is a single atomic operation; search does not go dark.
- **6a.** Grace period elapses; old collection dropped; disk space reclaimed.

**⚠ UNDOCUMENTED:** This workflow is strongly implied by INV-10 (model registry as source of truth) and the Milvus partition architecture, but is **not documented in `docs/`**. This is a dangerous gap — an admin who changes the model without a documented reindex procedure will break search silently.

---

## WF-7 — Admin updates a service

Routine deploy path for pulling updated code / config to a running installation.

### Steps

1. Admin pulls latest busibox code to admin workstation.
2. Admin runs `busibox` CLI → Manage → Redeploy → selects service(s).
3. CLI runs `make manage SERVICE={svc} ACTION=redeploy` inside the manager container.
4. Ansible:
   - a. Pulls the configured ref on the target host.
   - b. Rebuilds the service image if Dockerfile or dependencies changed.
   - c. Injects current vault secrets.
   - d. Restarts the container; waits for health-check to pass.
   - e. If health-check fails, reverts to the previous image and alerts.
5. CLI reports status.

### Observables

- **2a.** CLI action logged in admin's terminal history; vault-keys file read, decrypted, piped.
- **4a.** `git rev-parse HEAD` on target host matches the intended ref.
- **4b.** `docker images` shows the new image (for Docker backend); LXC systemd unit updated (for Proxmox).
- **4c.** Container has fresh env-from-vault variables (no stale secret from previous deploy).
- **4d.** Health endpoint returns 200 within timeout; service logs show successful startup.
- **4e.** (Failure path) Container rolls back to previous image without admin intervention; CLI returns non-zero.

**⚠ UNDOCUMENTED:** The exact rollback mechanism on failed redeploy (does Ansible re-tag the previous image? keep the previous container around? use a blue-green swap?) is not explicit in the docs.

---

## Maintenance

When adding a new workflow:
1. Assign the next WF-N number.
2. State the workflow's purpose in one sentence.
3. Number every discrete step; keep steps atomic (one actor, one observable boundary).
4. Letter every observable, positioned next to the step that produces it.
5. Include a verification procedure (smoke test, script, or manual sequence).
6. Mark any step whose behavior is not specified as **⚠ UNDOCUMENTED**.

Workflows are evergreen documentation — they outlive the code that implements them. When refactoring, preserve the workflow's observables even if the implementation changes.
