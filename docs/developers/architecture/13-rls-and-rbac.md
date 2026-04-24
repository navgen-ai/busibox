---
title: "RLS and RBAC in Busibox"
category: "developer"
order: 14
description: "How Row-Level Security (RLS) and Role-Based Access Control (RBAC) combine to enforce data and operation authorization across Busibox"
published: true
---

# RLS and RBAC in Busibox

**Created**: 2026-04-23
**Status**: Active
**Category**: Architecture
**Related Docs**:
- `architecture/03-authentication.md` — Zero Trust, OAuth2 token exchange, key management
- `architecture/11-document-sharing.md` — Visibility modes, `document_roles`, sharing patterns
- `.cursor/rules/003-zero-trust-authentication.md` — Authentication patterns
- `diagrams/rls-and-rbac.drawio` — Flowcharts and state diagrams for every use case below

---

## 1. Two-Dimensional Access Control

Busibox separates authorization into two orthogonal dimensions. Any request must satisfy **both**:

| Dimension | Mechanism | Question Answered | Enforced By |
|-----------|-----------|-------------------|-------------|
| **Operations** (RBAC) | OAuth2 scopes on roles | *"What am I allowed to do?"* | Service code (`has_scope`, `require_scope`) |
| **Data** (RLS) | Role membership + row policies | *"Which rows can I see/modify?"* | PostgreSQL `FORCE ROW LEVEL SECURITY` |

A user with `data.read` scope but no role match for a document will still get a 404 — the row is filtered out by RLS before the route handler ever sees it. Conversely, a user with a matching role but no `data.write` scope gets rejected at the service layer before the query runs.

---

## 2. RBAC (Role-Based Access Control)

### 2.1 Role Categories

Three role categories exist, distinguished by naming pattern:

| Category | Pattern | Created By | Purpose |
|----------|---------|------------|---------|
| **User Roles** | No prefix (e.g., `Admin`, `User`, `Finance`) | Admin API (`/admin/roles`) | Organization-wide access, carry OAuth2 scopes |
| **App Roles** | `app:<app-name>` (e.g., `app:busibox-workforce`) | Admin API, auto-created on app deploy | Own app document collections |
| **Team/Sub-Roles** | `app:<app-name>:<entity>` (e.g., `app:busibox-workforce:employees-team`) | Self-service API (`POST /roles`) | Fine-grained per-entity access |

User roles carry scopes. The built-in `Admin` role has wildcard `*`, which matches every scope including `data.admin`. App roles and team roles are primarily data-access tags; they typically carry no scopes.

### 2.2 Schema

```
authz_roles                       authz_user_roles
├── id (UUID PK)                  ├── user_id (FK → authz_users)
├── name (TEXT UNIQUE)            ├── role_id (FK → authz_roles)
├── description                   └── PRIMARY KEY (user_id, role_id)
├── scopes (TEXT[])
├── created_at, updated_at
```

Sources:
- `srv/authz/src/schema.py` — table definitions
- `srv/authz/src/routes/oauth.py:94-158` — bootstrap of `Admin` and `User` roles on startup
- `srv/authz/src/routes/oauth.py:257-339` — `ADMIN_EMAILS` → `Admin` role assignment

### 2.3 Scope Enforcement

Scopes are aggregated from **all** of a user's roles at token-exchange time and embedded in the access token. Services check them at the route layer:

```python
# srv/data/src/api/routes/files.py
@router.post("/files", dependencies=[Depends(ScopeChecker("data.write"))])
async def upload(...):
    ...
```

Wildcard matching: `*` matches any scope; `data.*` matches `data.read`, `data.write`, `data.delete`. The `Admin` role's single `*` scope therefore grants all operations. See `srv/shared/busibox_common/auth.py:633-726`.

| Service | Enforced? | Scopes |
|---------|-----------|--------|
| `data-api` | Yes | `data.read`, `data.write`, `data.delete`, `data.admin` |
| `search-api` | Helpers defined, not yet wired to routes | `search.read`, `search.write`, `search.delete` |
| `agent-api` | Helpers defined, not yet wired to routes | `agent.read`, `agent.write`, `agent.delete`, `agent.execute` |
| `authz` | Yes | `authz.roles.write`, `authz.users.read`, etc. |

### 2.4 Role Assignment

Two paths exist to bind users to roles:

1. **Admin API** — `POST /admin/user-roles`, requires `authz.roles.write`. Any role, any user. See `srv/authz/src/routes/admin.py`.
2. **Self-Service API** — `POST /roles`, requires only a valid session JWT. Role name must match `^app:[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*$`. Scopes restricted to the whitelist `{data:read, data:write, search:read, search:write, graph:read, graph:write, libraries:read, libraries:write}`. See `srv/authz/src/routes/roles.py:41-48`.

Membership changes do **not** retroactively update existing tokens. A user must re-exchange their session JWT (next request cycle) for the new role to appear in their access token.

---

## 3. RLS (Row-Level Security)

### 3.1 Tables with `FORCE ROW LEVEL SECURITY`

Every data table runs under `FORCE RLS`, meaning even the table owner (`busibox_user`) is subject to policies:

| Table | Purpose |
|-------|---------|
| `data_files` | File/document metadata with `visibility` and `owner_id` |
| `document_roles` | Many-to-many: which roles are tagged on which documents |
| `data_records` | Individual rows extracted from documents |
| `record_roles` | Many-to-many: which roles are tagged on which records |
| `data_chunks` | Text chunks for embeddings |
| `data_status` | Processing status |
| `processing_history` | Historical processing audit |

### 3.2 PostgreSQL Session Variables

Before every query, the data-api middleware sets session variables derived from the JWT:

| Variable | Source | Used By |
|----------|--------|---------|
| `app.user_id` | JWT `sub` | Owner checks (e.g., `owner_id = app.user_id`) |
| `app.user_role_ids_read` | JWT `roles[].id` as CSV | SELECT policies |
| `app.user_role_ids_create` | JWT `roles[].id` as CSV | INSERT policies |
| `app.user_role_ids_update` | JWT `roles[].id` as CSV | UPDATE/DELETE policies |
| `app.user_role_ids_delete` | JWT `roles[].id` as CSV | DELETE policies |
| `app.is_admin` | Set by app code **after** scope verification, never from JWT | Admin bypass policies |

Today all four `user_role_ids_*` hold the same CSV; they are separated to allow future granular CRUD tuning.

Implementation: `srv/shared/busibox_common/auth.py:546-626`, wired via `data_service.acquire_with_rls()` in `srv/data/src/api/services/data_service.py:76-110`.

### 3.3 `data_files` Policies

| Policy | Op | Condition |
|--------|----|-----------|
| `personal_docs_select` | SELECT | `visibility = 'personal' AND owner_id = app.user_id` |
| `shared_docs_select` | SELECT | `visibility = 'shared' AND EXISTS(document_roles matching user roles)` |
| `authenticated_docs_select` | SELECT | `visibility = 'authenticated' AND app.user_id IS NOT NULL` |
| `admin_docs_select` | SELECT | `app.is_admin = 'true'` |
| `data_files_insert` | INSERT | `owner_id = app.user_id` |
| `personal_docs_update` | UPDATE | `visibility = 'personal' AND owner_id = app.user_id` |
| `shared_docs_update` | UPDATE | `visibility = 'shared' AND (owner OR matching role)` |
| `authenticated_docs_update` | UPDATE | `visibility = 'authenticated' AND owner_id = app.user_id` |
| `admin_docs_update` | UPDATE | `app.is_admin = 'true'` |
| `personal_docs_delete` | DELETE | `visibility = 'personal' AND owner_id = app.user_id` |
| `shared_docs_delete` | DELETE | `visibility = 'shared' AND user can delete all bound roles` |
| `authenticated_docs_delete` | DELETE | `visibility = 'authenticated' AND owner_id = app.user_id` |
| `admin_docs_delete` | DELETE | `app.is_admin = 'true'` |

> `SELECT ... FOR UPDATE` requires **both** SELECT and UPDATE policies to pass. Admins updating other users' documents must use the `acquire_admin` path so `app.is_admin` is set for both.

### 3.4 `document_roles` and `record_roles` Policies

`document_roles` is itself RLS-guarded — a user can only manage role-to-document bindings for roles they hold:

| Policy | Op | Condition |
|--------|----|-----------|
| `document_roles_select` | SELECT | `role_id IN user_role_ids_read` |
| `document_roles_insert` | INSERT | `role_id IN user_role_ids_create` |
| `document_roles_update` | UPDATE | `role_id IN user_role_ids_update` |
| `document_roles_delete` | DELETE | `role_id IN user_role_ids_update` |
| `admin_document_roles_*` | ALL | `app.is_admin = 'true'` |

`data_records` supports three visibility modes — `inherit`, `personal`, `shared` — with parallel policies (inherit via parent document, owner check, or matching `record_roles`). See `docs/developers/architecture/11-document-sharing.md` for the complete list.

### 3.5 Admin Bypass

`app.is_admin` is **never** read from the JWT. Application code sets it only after verifying the caller holds the `data.admin` scope (only `Admin`'s `*` wildcard matches today):

```python
async with data_service.acquire_with_rls(request) as conn:
    if user_context.has_scope("data.admin"):
        await conn.execute("SET app.is_admin = 'true'")
```

Two activation paths:
- **`acquire_admin` context manager** — sets for the whole connection; used for admin list endpoints.
- **`SET LOCAL app.is_admin = 'true'`** — transaction-scoped; used for narrow operations (e.g., an owner managing roles on a document they own but where RLS on `document_roles` would otherwise fail).

Admin policies use only `current_setting('app.is_admin')` — never cross-table subqueries — to keep the bypass unambiguous.

---

## 4. Visibility Modes

| UI Label | `data_files.visibility` | `document_roles` | Who Can Access |
|----------|------------------------|-------------------|----------------|
| **Personal** | `personal` | empty | Owner only |
| **App** | `shared` | app role(s) | Users whose JWT contains a matching `app:<name>` role |
| **Shared** | `shared` | user/team role(s) | Users whose JWT contains a matching user or team role |
| **Authenticated** | `authenticated` | empty | Any authenticated user |

"App" and "Shared" share the `shared` DB value; the UI label is derived from whether the bound roles are `app:*` (two segments) or user/team roles.

Critical invariant: **a role in `document_roles` grants access only if the user also holds that role in their JWT**. Granting a user a role without also ensuring the role is present on the documents is a common bug.

---

## 5. Use Cases (Full Lifecycles)

Each use case below is visualized in `diagrams/rls-and-rbac.drawio` — one flowchart per interaction, plus a state diagram for anything with meaningful state transitions.

### UC-1: User Login & JWT Issuance

**Goal**: Establish authenticated identity for a user.

1. Browser hits `/auth/passkeys/authenticate`, `/auth/totp/verify`, or magic-link verify.
2. AuthZ verifies the credential (public endpoints — they *are* the authentication).
3. AuthZ looks up or creates `authz_users` row; fetches `authz_user_roles` → `authz_roles`.
4. AuthZ builds claims: `iss=busibox-authz`, `sub=user_id`, `aud=busibox-portal`, `typ=session`, `roles=[{id,name}...]`, `exp=now+7d`, unique `jti`.
5. AuthZ signs with RS256 private key and returns JWT.
6. Portal sets `busibox-session` cookie.

Source: `srv/authz/src/routes/auth.py:85-145`.

**State**: `Anonymous → Authenticated (session JWT)`. Revocation via `jti` in `authz_sessions`; expiry at 7 days (`AUTHZ_SESSION_TOKEN_TTL`).

### UC-2: API Request → JWT Validation → RBAC Check → RLS Query

**Goal**: Every data-access call filters both by scope (RBAC) and by row visibility (RLS).

1. Client sends `Authorization: Bearer <access_token>` (obtained from UC-7).
2. Service fetches JWKS (cached) and verifies RS256 signature + `iss`, `exp`, `aud`.
3. `JWTAuthMiddleware` populates `request.state.user_context` with `user_id`, `roles`, `scopes`.
4. Route dependency `ScopeChecker("data.read")` runs. Missing scope → 403.
5. Handler calls `async with data_service.acquire_with_rls(request) as conn:` which:
   - `SET app.user_id = '<user_id>'`
   - `SET app.user_role_ids_read/create/update/delete = '<csv>'`
   - If `user_context.has_scope("data.admin")`: `SET app.is_admin = 'true'`
6. Query runs. Postgres applies matching policies. Rows failing all policies are silently dropped.
7. Handler returns filtered results. A user denied by RLS sees an empty list or 404, never a 403.

### UC-3: Document Upload With Visibility

**Goal**: Create a `data_files` row and optionally bind roles that grant access.

1. User uploads file via `POST /files` with scope `data.write`.
2. Route handler writes object to MinIO at `personal/{user_id}/{file_id}/...` (or `role/{role_id}/...` for shared).
3. `INSERT INTO data_files (..., owner_id=app.user_id, visibility=<chosen>)`. The `data_files_insert` policy forces `owner_id = app.user_id` — users cannot forge ownership.
4. If `visibility='shared'`, for each chosen role: `INSERT INTO document_roles (file_id, role_id)`. The `document_roles_insert` policy requires each `role_id` to be in `user_role_ids_create`.
5. If encryption is enabled, AuthZ keystore wraps a per-file DEK with each bound role's KEK (see `architecture/03-authentication.md` §Envelope Encryption).

### UC-4: Document Retrieval Under Each Visibility Mode

Four policy-evaluation paths for SELECT (first match wins — policies are `OR`ed):

| Scenario | Matching Policy | Result |
|----------|----------------|--------|
| Owner reading own personal doc | `personal_docs_select` | Visible |
| Non-owner reading someone else's personal doc | None | Filtered (404) |
| User holds role that tags shared doc | `shared_docs_select` | Visible |
| User lacks any matching role on shared doc | None | Filtered (404) |
| Any authenticated user reading `authenticated` doc | `authenticated_docs_select` | Visible |
| Admin via `acquire_admin` | `admin_docs_select` | Visible |

### UC-5: Role Creation & Assignment

**Admin path**: `POST /admin/roles` + `POST /admin/user-roles`, gated by `authz.roles.write` scope. Any name, any scopes.

**Self-service path**: `POST /roles` with session JWT. Role name must match `^app:<app>:<entity>$`. Scopes restricted to whitelist.

**Lifecycle**:
```
(non-existent) → [role row in authz_roles]
              → [members via authz_user_roles]
              → [bound to documents via document_roles]
```

A user gains effective access only when all three of the following are true:
1. Role exists in `authz_roles`
2. User is in `authz_user_roles` for that role
3. Role is in `document_roles` for the documents in question
4. User has re-exchanged their session JWT since step 2

### UC-6: Team Member Addition (Two-Step Gotcha)

Adding a user to a role is **not** sufficient:

1. `POST /roles/{roleId}/members` → `INSERT INTO authz_user_roles`.
2. `POST /documents/{docId}/roles` → `INSERT INTO document_roles` **for every document the team should access**.

Skipping step 2 silently breaks access. Helper: `addRoleToDocuments(dataToken, roleId, docIds[])` in `@jazzmind/busibox-app/lib/data/sharing`.

### UC-7: Token Exchange (App-Scoped vs Default)

The same session JWT produces different access tokens depending on whether `resource_id` is provided:

| Exchange Type | Use Case | Roles Included |
|---------------|----------|----------------|
| Default (no `resource_id`) | `requireAuthWithTokenExchange(request, 'data-api')` | **All** of the user's roles |
| App-scoped (with `resource_id`) | Portal SSO launch into a specific app | Only roles bound to the app |

Scopes are re-aggregated from the authz DB at exchange time — never carried over from the incoming token. This means revoking a role takes effect on the next exchange (max 15 min for access tokens, the `AUTHZ_ACCESS_TOKEN_TTL`).

Source: `srv/authz/src/routes/oauth.py:500-634`.

### UC-8: Admin Override

Admin operations need both a scope and an RLS bypass:

1. Admin's session JWT → token exchange → access token with aggregated `*` scope.
2. Service verifies `data.admin` scope via `has_scope(request, "data.admin")`.
3. Service enters `acquire_admin`: `SET app.is_admin = 'true'` on the connection (or `SET LOCAL` inside a transaction).
4. Admin-specific RLS policies (`admin_docs_*`, `admin_document_roles_*`, `admin_records_*`) match on `current_setting('app.is_admin') = 'true'`.
5. Connection closes → `app.is_admin` drops with the session.

Admin bypass is never automatic. Any route that wants to see all rows must explicitly opt in with `acquire_admin` **and** have verified the scope.

### UC-9: Document Visibility State Transitions

A document moves through these states over its lifetime:

```
   Uploaded (visibility='personal', no document_roles)
        │
        ├─→ add app role + set visibility='shared'     → SharedApp
        ├─→ add team role + set visibility='shared'    → SharedTeam
        └─→ set visibility='authenticated'             → Authenticated

   SharedApp ⇄ SharedTeam    (add/remove additional roles)
   SharedApp → Personal       (remove all roles + visibility='personal')
   Authenticated → Personal   (visibility='personal')
   Any → Deleted
```

A `BEFORE DELETE` trigger (`ensure_document_has_roles`) prevents deleting the last role from a `shared` document. The `set_document_roles` service routes around this by first flipping visibility to `personal`, then mutating roles, then setting the final visibility — all within one transaction.

---

## 6. Common Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Added user to role but they still see 404 | RLS rejects on `document_roles` miss | Also add role to `document_roles` for every relevant doc |
| Admin sees empty list | Scope verified but `acquire_admin` not used | Use `acquire_admin` context manager, not `acquire_with_rls` |
| UPDATE works, `SELECT ... FOR UPDATE` fails for admin | Needs both SELECT and UPDATE policies | Admin must also satisfy admin_select policy → set `app.is_admin` |
| Revoked role still grants access | Access token cached (≤15 min TTL) | Wait for exchange refresh, or invalidate session (JTI) |
| Self-service role creation rejected | Name doesn't match pattern or scope not whitelisted | Use `app:<app>:<entity>` and stick to whitelisted scopes |
| Deleting last role from shared doc errors out | `ensure_document_has_roles` trigger | Flip to `personal` first, or use `set_document_roles` helper |

---

## 7. Key File & Line References

| Concern | File |
|---------|------|
| Session JWT signing | `srv/authz/src/routes/auth.py:85-145` |
| Bootstrap `Admin`/`User` roles | `srv/authz/src/routes/oauth.py:94-158` |
| `ADMIN_EMAILS` → `Admin` assignment | `srv/authz/src/routes/oauth.py:257-339` |
| Token exchange | `srv/authz/src/routes/oauth.py:500-634` |
| Self-service roles | `srv/authz/src/routes/roles.py` |
| Admin RBAC endpoints | `srv/authz/src/routes/admin.py` |
| JWT validation + UserContext | `srv/shared/busibox_common/auth.py:149-255` |
| Scope checking | `srv/shared/busibox_common/auth.py:633-726` |
| RLS session-var setup | `srv/shared/busibox_common/auth.py:546-626` |
| `acquire_with_rls` / `acquire_admin` | `srv/data/src/api/services/data_service.py:76-110` |
| RLS policy SQL | `provision/ansible/roles/postgres/data/migrations/002_add_rls_policies.sql` |
| Authz schema | `srv/authz/src/schema.py` |
| Sharing helpers | `@jazzmind/busibox-app/lib/data/sharing` |
