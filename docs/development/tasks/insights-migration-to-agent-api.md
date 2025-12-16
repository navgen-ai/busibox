# Insights Migration: search-api → agent-api

**Status**: Planning  
**Priority**: High  
**Created**: 2025-12-16  
**Related**: Chat Architecture Refactor

## Overview

Move insights (agent memories/context) from search-api to agent-api where they belong architecturally.

## Rationale

**Why agent-api?**
- Insights are agent memories/context, not search functionality
- Agents need direct access to their memories
- Keeps all agent-related state in one service
- search-api should focus purely on search (documents, web)

**Current State** (Incorrect):
```
search-api:
  - Document search (BM25, vector, hybrid) ✓ Correct
  - Web search ✓ Correct  
  - Chat insights ✗ Wrong place
```

**Target State** (Correct):
```
agent-api:
  - Agent operations ✓
  - Chat history ✓ (to be added)
  - Chat insights/memories ✓ (to be moved)

search-api:
  - Document search ✓
  - Web search ✓
```

## Technical Details

### Current Implementation (search-api)

**Files**:
- `busibox/srv/search/src/services/insights_service.py`
- `busibox/srv/search/src/api/routes/insights.py`
- `busibox/srv/search/src/shared/schemas.py` (insight schemas)

**Endpoints**:
```
POST   /insights/init
POST   /insights
POST   /insights/search
DELETE /insights/conversation/{conversation_id}
DELETE /insights/user/{user_id}
GET    /insights/stats/{user_id}
```

**Milvus Collection**: `chat_insights`

### Target Implementation (agent-api)

**New Files**:
- `agent-server/src/services/insights_service.py` (copy from search-api)
- `agent-server/src/api/routes/insights.py` (copy from search-api)
- `agent-server/src/schemas/insights.py` (copy schemas)

**Same Endpoints** (maintain API compatibility):
```
POST   /insights/init
POST   /insights
POST   /insights/search
DELETE /insights/conversation/{conversation_id}
DELETE /insights/user/{user_id}
GET    /insights/stats/{user_id}
```

**Same Milvus Collection**: `chat_insights` (no data migration needed)

## Migration Steps

### Phase 1: Implement in agent-api (Parallel)

1. **Copy insights code to agent-api**:
   ```bash
   # Copy service
   cp busibox/srv/search/src/services/insights_service.py \
      agent-server/src/services/insights_service.py
   
   # Copy routes
   cp busibox/srv/search/src/api/routes/insights.py \
      agent-server/src/api/routes/insights.py
   
   # Copy schemas (extract insight-related)
   # Create agent-server/src/schemas/insights.py
   ```

2. **Update agent-api main.py**:
   ```python
   from api.routes import insights
   
   app.include_router(insights.router, prefix="/insights", tags=["insights"])
   ```

3. **Update agent-api requirements.txt**:
   ```
   # Already has pymilvus from existing code
   ```

4. **Test agent-api insights**:
   ```bash
   # Test all endpoints
   curl -X POST http://agent-lxc:8000/insights/init
   curl -X POST http://agent-lxc:8000/insights -d '{"insights": [...]}'
   curl -X POST http://agent-lxc:8000/insights/search -d '{"query": "...", "userId": "..."}'
   ```

5. **Update OpenAPI specs**:
   - Add insights endpoints to `openapi/agent-api.yaml`
   - Mark as deprecated in `openapi/search-api.yaml`

### Phase 2: Update busibox-app (Backward Compatible)

1. **Add environment variable support**:
   ```typescript
   // busibox-app/src/lib/insights/client.ts
   const INSIGHTS_API_URL = 
     process.env.INSIGHTS_API_URL || 
     process.env.AGENT_API_URL || 
     process.env.SEARCH_API_URL || // Fallback for backward compat
     'http://localhost:8000'; // Default to agent-api
   ```

2. **Update client to use new URL**:
   ```typescript
   export async function insertInsights(
     insights: ChatInsight[],
     tokenManager?: TokenManager
   ): Promise<void> {
     await callService(
       'Insights API Insert',
       '/insights',
       {
         method: 'POST',
         body: JSON.stringify({ insights }),
         baseUrl: INSIGHTS_API_URL, // Use configurable URL
       },
       tokenManager
     );
   }
   ```

3. **Publish busibox-app v2.1.0**:
   - Bump version
   - Update changelog
   - Publish to npm

### Phase 3: Update Consuming Apps & Remove from search-api (Direct Cutover)

1. **Update environment variables**:
   ```bash
   # ai-portal .env
   INSIGHTS_API_URL=http://agent-lxc:8000
   # or
   AGENT_API_URL=http://agent-lxc:8000  # Will be used for insights
   ```

2. **Update package.json**:
   ```json
   {
     "dependencies": {
       "@jazzmind/busibox-app": "^2.1.0"
     }
   }
   ```

3. **Remove insights from search-api**:
   ```bash
   rm busibox/srv/search/src/services/insights_service.py
   rm busibox/srv/search/src/api/routes/insights.py
   # Remove insights schemas from src/shared/schemas.py
   # Update main.py to remove insights router
   ```

4. **Deploy everything in one go**:
   ```bash
   # Deploy agent-api with insights
   cd provision/ansible
   make deploy-agent
   
   # Deploy updated apps
   make deploy-ai-portal
   
   # Deploy search-api without insights
   make deploy-search
   
   # Test insights functionality
   ```

## Testing Checklist

### agent-api Tests
- [ ] Initialize insights collection
- [ ] Insert insights
- [ ] Search insights by query
- [ ] Delete conversation insights
- [ ] Delete user insights
- [ ] Get insight stats
- [ ] Verify Milvus connection
- [ ] Test with authentication

### busibox-app Tests
- [ ] Insights client uses correct URL
- [ ] Environment variable override works
- [ ] Backward compatibility maintained
- [ ] All insight operations work

### Integration Tests
- [ ] ai-portal can insert insights
- [ ] ai-portal can search insights
- [ ] agent-client can access insights
- [ ] Insights persist across restarts

## Rollback Plan

If issues occur:

1. **Immediate**: Set `INSIGHTS_API_URL=http://search-lxc:8001` in apps
2. **Keep search-api insights** until fully stable
3. **Gradual migration**: Move one app at a time
4. **Monitor**: Check logs for errors

## Benefits

1. **Correct Architecture**: Insights with agents where they belong
2. **Better Cohesion**: All agent state in one place
3. **Clearer Responsibilities**: Each service has clear purpose
4. **Easier Development**: Agent developers have all context in one service

## Timeline (Direct Cutover)

- **Day 1**: Implement in agent-api, test ✅
- **Day 2**: Update busibox-app with configurable URL
- **Day 3**: Test all changes in test environment
- **Day 4**: Coordinated deployment to production (all services at once)
- **Day 5**: Monitor and verify

## Related Documents

- `chat-architecture-refactor.md` - Overall chat refactor plan
- `openapi/agent-api.yaml` - Agent API specification
- `openapi/search-api.yaml` - Search API specification

