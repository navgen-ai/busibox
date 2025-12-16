# Authz Test Client Setup Required

**Date**: 2025-12-15
**Status**: Configuration Required
**Priority**: High - Blocks all chat integration tests

## Problem

The busibox-app integration tests cannot acquire auth tokens because the test client credentials are not authorized to perform token exchange.

### Error

```
Auth token acquisition failed: Failed to exchange token: 403 {"detail":"unauthorized_client_scope"}
```

### Root Cause

The authz service is rejecting token exchange requests from the test client, even for basic role scopes like `["user"]`.

## Current Test Client Configuration

The tests use these environment variables (from `.env`):

```bash
AUTHZ_TEST_CLIENT_ID=<client-id>
AUTHZ_TEST_CLIENT_SECRET=<client-secret>
```

Or fall back to:

```bash
AUTHZ_BOOTSTRAP_CLIENT_ID=<bootstrap-client-id>
AUTHZ_BOOTSTRAP_CLIENT_SECRET=<bootstrap-secret>
```

## Required Fix

The test client needs to be configured in the authz service with permission to:

1. **Perform token exchange** (`urn:ietf:params:oauth:grant-type:token-exchange`)
2. **Request role-based scopes** (at minimum: `user`, `admin`)
3. **Target agent-api audience**

### Configuration Steps

#### Option 1: Grant Permissions to Existing Test Client

```bash
# SSH to authz container
ssh root@10.96.201.210  # Test environment
# or
ssh root@10.96.200.210  # Production environment

# Grant token exchange permission to test client
# (Exact commands depend on authz implementation)

# The test client needs:
# - grant_type: token-exchange
# - allowed_scopes: ["user", "admin", "*"]  # Or specific scopes
# - allowed_audiences: ["agent-api", "ingest-api", "search-api"]
```

#### Option 2: Use Bootstrap Client

The bootstrap client likely already has all permissions. Update `.env` to use it:

```bash
# In busibox-app/.env
AUTHZ_TEST_CLIENT_ID=${AUTHZ_BOOTSTRAP_CLIENT_ID}
AUTHZ_TEST_CLIENT_SECRET=${AUTHZ_BOOTSTRAP_CLIENT_SECRET}
```

#### Option 3: Create New Integration Test Client

Create a dedicated client for integration tests with full permissions:

```bash
# Create new client in authz service
client_id: "integration-test-client"
client_secret: "<secure-random-secret>"
grant_types: ["client_credentials", "urn:ietf:params:oauth:grant-type:token-exchange"]
allowed_scopes: ["*"]  # Or specific: ["user", "admin", "agent.execute", "chat.read", "chat.write"]
allowed_audiences: ["*"]  # Or specific: ["agent-api", "ingest-api", "search-api", "authz"]
```

## Test Flow

The tests use this OAuth flow:

1. **Get Subject Token**:
   ```
   POST /oauth/token
   grant_type=client_credentials
   client_id=<test-client-id>
   client_secret=<test-client-secret>
   audience=authz
   ```

2. **Exchange for User Token**:
   ```
   POST /oauth/token
   grant_type=urn:ietf:params:oauth:grant-type:token-exchange
   client_id=<test-client-id>
   client_secret=<test-client-secret>
   subject_token=<token-from-step-1>
   requested_subject=<user-id>
   audience=agent-api
   scope=user  # Role-based scope
   ```

The second step is failing with `403 unauthorized_client_scope`.

## Impact

### Currently Blocked (10 tests)

All chat client integration tests in `busibox-app/tests/chat-client.test.ts`:

1. Model Operations › should get available models
2. Conversation Management › should create a new conversation
3. Conversation Management › should list conversations
4. Chat Message Operations › should send a chat message (non-streaming)
5. Chat Message Operations › should stream a chat message
6. Chat Message Operations › should get conversation history
7. Advanced Features › should send message with web search enabled
8. Advanced Features › should send message with model selection
9. Error Handling › should handle invalid conversation ID
10. Error Handling › should handle missing auth token

### Currently Passing (102 tests)

All other integration tests work fine:
- Ingest API tests
- Embeddings tests
- Insights API tests
- RBAC tests
- Audit tests
- Search tests

## Verification

After configuring the test client, verify with:

```bash
cd /path/to/busibox-app

# Test just the chat client
npm test -- tests/chat-client.test.ts

# Expected output:
# Test Suites: 1 passed, 1 total
# Tests:       10 passed, 10 total
```

## Future: Granular OAuth Scopes

Currently using role-based scopes (`user`, `admin`). In the future, we'll implement granular OAuth scopes:

- `agent.execute` - Execute agent operations
- `chat.read` - Read chat conversations and messages
- `chat.write` - Create conversations and send messages
- `ingest.read` - Read ingested documents
- `ingest.write` - Upload and process documents
- `search.read` - Perform searches
- `insights.read` - Read user insights/memories
- `insights.write` - Create/update insights

When implementing granular scopes, the test client will need to be granted these as well.

## Related Files

- `busibox-app/tests/helpers/auth.ts` - Token acquisition logic
- `busibox-app/tests/chat-client.test.ts` - Failing tests
- `busibox/srv/agent/app/auth/tokens.py` - Agent-API token validation
- `busibox/srv/agent/app/auth/dependencies.py` - Auth dependency injection

## Summary

**Action Required**: Configure the authz service to allow the test client to perform token exchange with role-based scopes.

**Estimated Time**: 10-15 minutes

**Expected Result**: All 112 tests passing (100% pass rate)
