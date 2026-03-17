---
title: "Document Sharing"
category: "developer"
order: 12
description: "Unified document sharing model with team roles, visibility modes, and RLS-based access control"
published: true
---

# Document Sharing

**Created**: 2026-03-17  
**Last Updated**: 2026-03-17  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/03-authentication.md`  
- `architecture/07-apps.md`

---

## Overview

Busibox provides a unified document sharing model that allows apps to control who can access data documents. The model supports three visibility modes and two sharing patterns, backed by authz self-service roles and data-api PostgreSQL Row-Level Security (RLS).

## Visibility Modes

| Mode | `data_files.visibility` | `document_roles` | Who Can Access |
|------|------------------------|-------------------|----------------|
| **Private** | `personal` | empty | Only the document owner (`owner_id`) |
| **Shared** | `authenticated` | N/A | Any authenticated user in the app |
| **Team** | `shared` | team role(s) | Only users whose JWT contains a matching role |

### How RLS Enforces Access

The data-api sets PostgreSQL session variables from the JWT before each query:

```
app.user_id              → owner_id check (personal docs)
app.user_role_ids_read   → role membership check (shared docs)
app.user_role_ids_create → role membership check (insert)
app.user_role_ids_update → role membership check (update)
app.user_role_ids_delete → role membership check (delete)
```

RLS policies in `data_files`:
- **personal**: `owner_id = app.user_id`
- **shared**: user has a role in `document_roles` matching one of their `app.user_role_ids_*`
- **authenticated**: any non-null `app.user_id`

## Sharing Patterns

### App-Level Sharing

One team role for the entire app. All users in the team see all the app's data.

**Example**: Workforce app — a single `app:busibox-workforce:employees-team` role grants access to the employees document.

```
ensureTeamRole(ssoToken, 'busibox-workforce', 'employees')
→ creates role: app:busibox-workforce:employees-team
```

### Entity-Level Sharing

One team role per entity (e.g., per campaign, per project). Different entities can have different teams.

**Example**: Recruiter app — each campaign gets its own role, so different reviewers can be assigned to different campaigns.

```
ensureTeamRole(ssoToken, 'busibox-recruiter', 'campaign-frontend-dev-reviewer')
→ creates role: app:busibox-recruiter:campaign-frontend-dev-reviewer-team
```

## Architecture

```
┌─────────────────────────────────────┐
│          App (Next.js)              │
│                                     │
│  SSO Token ──► Authz Self-Service   │
│    (busibox-session cookie)         │
│    • Create/find team roles         │
│    • Add/remove members             │
│    • Search users                   │
│                                     │
│  Data Token ──► Data API            │
│    (from token exchange)            │
│    • Set document visibility        │
│    • Add roles to documents         │
│    • CRUD with RLS enforcement      │
└─────────────────────────────────────┘
         │                    │
         ▼                    ▼
┌────────────────┐  ┌────────────────┐
│   Authz        │  │   Data API     │
│                │  │                │
│ authz_roles    │  │ data_files     │
│ authz_user_    │  │   visibility   │
│   roles        │  │   owner_id     │
│                │  │                │
│ Self-service   │  │ document_roles │
│ endpoints:     │  │   role_id      │
│ POST /roles    │  │   file_id      │
│ GET /roles     │  │                │
│ /roles/{id}/   │  │ PostgreSQL RLS │
│   members      │  │ enforces access│
│ /roles/users/  │  │                │
│   search       │  │                │
└────────────────┘  └────────────────┘
```

## Token Types

**Two tokens are always needed for sharing operations:**

1. **SSO Session JWT** (`busibox-session` cookie)
   - Used for: authz self-service endpoints (role CRUD, member management, user search)
   - Get with: `getSSOTokenFromRequest(request)` from `@jazzmind/busibox-app/lib/data/sharing`
   - Requirement: Must have `typ: "session"` claim, signed by authz

2. **Data-API Token** (from `requireAuthWithTokenExchange(request, 'data-api')`)
   - Used for: data-api document/library role management, all CRUD operations
   - Contains: user's role IDs in the `roles` claim, used by RLS

## Busibox-App Sharing API

All sharing helpers live in `@jazzmind/busibox-app/lib/data/sharing`:

### Role Management

| Function | Token | Description |
|----------|-------|-------------|
| `ensureTeamRole(ssoToken, appName, entityName)` | SSO | Create or find a role named `app:{appName}:{entityName}-team` |
| `verifyRoleExists(ssoToken, roleId)` | SSO | Check if a role still exists |

### Document Role Management

| Function | Token | Description |
|----------|-------|-------------|
| `addRoleToDocuments(dataToken, roleId, docIds[])` | Data | Add role to documents (idempotent) |
| `removeRoleFromDocuments(dataToken, roleId, docIds[])` | Data | Remove role from documents |
| `addRoleToLibrary(dataToken, roleId, libraryId)` | Data | Add role to a library |

### Member Management

| Function | Token | Description |
|----------|-------|-------------|
| `listTeamMembers(ssoToken, roleId)` | SSO | List role members |
| `addTeamMember(ssoToken, roleId, userId)` | SSO | Add user to role |
| `removeTeamMember(ssoToken, roleId, userId)` | SSO | Remove user from role |
| `searchUsers(ssoToken, query)` | SSO | Search users by name/email |

### Visibility Management

| Function | Token | Description |
|----------|-------|-------------|
| `setDocumentVisibility(dataToken, docIds[], mode, roleId?)` | Data | Switch documents between modes |
| `resolveVisibilityMode(visibility, roleIds, ...)` | — | Determine mode from doc roles |

## Critical: Adding Team Members

When a team member is added, the team role must be present in the `document_roles` table for **every document** the team should access. If a document is missing the role, RLS will deny access even though the user has the role in their JWT.

For app-level sharing, add the role to all app documents:
```typescript
await addRoleToDocuments(dataToken, role.roleId, Object.values(documentIds));
```

For entity-level sharing, add the role to both entity-specific and app-level documents:
```typescript
await addRoleToDocuments(dataToken, role.roleId, [
  appDocIds.campaigns,      // App-level: campaigns container
  appDocIds.activities,      // App-level: activities container
  campaign.schemaDocumentId, // Entity-specific: candidate data
]);
await addRoleToLibrary(dataToken, role.roleId, campaign.libraryId);
```

## Reference Implementations

- **busibox-template**: `lib/sharing.ts`, `app/api/team/route.ts`, `app/api/settings/visibility/route.ts`
- **busibox-workforce**: App-level sharing in `app/api/settings/visibility/route.ts` and `app/api/settings/team/route.ts`
- **busibox-recruiter**: Entity-level sharing in `lib/campaign-setup.ts` and `app/api/campaigns/[id]/team/route.ts`

## Self-Service Role Naming Convention

All self-service roles follow the pattern:
```
app:{appName}:{entityName}-team
```

Examples:
- `app:busibox-workforce:employees-team`
- `app:busibox-recruiter:campaign-frontend-dev-reviewer-team`
- `app:my-app:data-team`

The `app:` prefix and naming pattern are enforced by authz. The `source_app` is extracted from the second segment for filtering (e.g., `GET /roles?app=busibox-recruiter`).
