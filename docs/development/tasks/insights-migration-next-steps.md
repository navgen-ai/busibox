# Insights Migration - Next Steps

**Status**: Ready for Phase 2  
**Date**: 2025-12-16  
**Previous**: [insights-migration-completed.md](./insights-migration-completed.md)

## Phase 1: ✅ COMPLETED

Insights functionality has been successfully migrated to agent-api. All endpoints are working and tested.

## Phase 2: Update busibox-app (Next)

### Goal
Make the busibox-app insights client configurable to support both search-api (legacy) and agent-api (new).

### Changes Needed

**File**: `busibox-app/src/lib/insights/client.ts`

```typescript
// Add configurable URL support
const INSIGHTS_API_URL = 
  process.env.INSIGHTS_API_URL || 
  process.env.AGENT_API_URL || 
  process.env.SEARCH_API_URL || // Fallback for backward compat
  'http://localhost:8000'; // Default to agent-api

// Update all insight functions to use INSIGHTS_API_URL
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

// Update other functions similarly:
// - searchInsights()
// - deleteConversationInsights()
// - deleteUserInsights()
// - getInsightStats()
// - flushInsightsCollection()
```

### Steps

1. **Update busibox-app client**:
   ```bash
   cd busibox-app
   # Edit src/lib/insights/client.ts
   # Add INSIGHTS_API_URL configuration
   # Update all insight functions
   ```

2. **Test locally**:
   ```bash
   npm run build
   npm test
   ```

3. **Update version**:
   ```json
   // package.json
   {
     "version": "2.1.0"
   }
   ```

4. **Update CHANGELOG**:
   ```markdown
   ## [2.1.0] - 2025-12-16
   
   ### Changed
   - Insights client now supports configurable API URL via environment variables
   - Defaults to agent-api (port 8000) instead of search-api (port 8001)
   - Maintains backward compatibility with SEARCH_API_URL fallback
   ```

5. **Publish to npm**:
   ```bash
   npm run build
   npm publish
   ```

## Phase 3: Update Consuming Apps

### ai-portal

**File**: `ai-portal/.env` (or deployment config)

```bash
# Add new environment variable
INSIGHTS_API_URL=http://agent-lxc:8000
# or
AGENT_API_URL=http://agent-lxc:8000
```

**Steps**:
```bash
cd ai-portal
npm install @jazzmind/busibox-app@^2.1.0
npm run build
# Deploy to test environment first
# Test insights functionality
# Deploy to production
```

### agent-client

Same process as ai-portal:

```bash
cd agent-client
# Update .env with INSIGHTS_API_URL or AGENT_API_URL
npm install @jazzmind/busibox-app@^2.1.0
npm run build
# Deploy and test
```

### Other Apps

Repeat for any other apps using insights:
1. Update environment variables
2. Update busibox-app dependency
3. Build and deploy
4. Test insights functionality

## Phase 4: Deprecate search-api Insights

**WAIT**: Only proceed after all apps are migrated and stable (1 month minimum).

### Steps

1. **Mark as deprecated** in search-api:

   **File**: `srv/search/src/api/routes/insights.py`
   
   ```python
   import warnings
   
   @router.post("/insights/init")
   async def initialize_collection(request: Request):
       """
       DEPRECATED: This endpoint has moved to agent-api.
       Please update your application to use:
       http://agent-lxc:8000/insights/init
       """
       warnings.warn(
           "Insights endpoints in search-api are deprecated. "
           "Use agent-api instead: http://agent-lxc:8000/insights",
           DeprecationWarning
       )
       
       # Add deprecation headers
       response = # ... existing code ...
       response.headers["X-Deprecated"] = "true"
       response.headers["X-Deprecated-Replacement"] = "http://agent-lxc:8000/insights"
       return response
   ```

2. **Update all insights endpoints** with deprecation warnings

3. **Monitor usage**:
   - Check logs for deprecated endpoint usage
   - Verify all apps have migrated
   - Wait 1 month

4. **Remove code**:
   ```bash
   cd srv/search
   rm src/services/insights_service.py
   rm src/api/routes/insights.py
   # Remove insights schemas from src/shared/schemas.py
   # Update main.py to remove insights router
   ```

## Testing Checklist

### Phase 2 (busibox-app)
- [ ] Insights client uses correct URL
- [ ] Environment variable override works
- [ ] Backward compatibility maintained (SEARCH_API_URL fallback)
- [ ] All insight operations work
- [ ] Package builds successfully
- [ ] Tests pass
- [ ] Published to npm

### Phase 3 (Consuming Apps)
- [ ] ai-portal can insert insights
- [ ] ai-portal can search insights
- [ ] ai-portal insights persist
- [ ] agent-client can access insights
- [ ] All apps updated to busibox-app v2.1.0
- [ ] All apps deployed to test environment
- [ ] All apps deployed to production

### Phase 4 (Deprecation)
- [ ] All apps migrated and stable for 1 month
- [ ] No usage of deprecated search-api endpoints
- [ ] Deprecation warnings added
- [ ] Code removed from search-api
- [ ] Tests updated
- [ ] Documentation updated

## Rollback Plan

If issues occur at any phase:

### Phase 2 Issues
- Revert busibox-app to previous version
- Apps continue using search-api

### Phase 3 Issues
- Set `INSIGHTS_API_URL=http://search-lxc:8001` in affected app
- Downgrade busibox-app if needed
- Investigate and fix issue
- Retry migration

### Phase 4 Issues
- Should not occur if previous phases tested properly
- If needed, restore insights code from git history

## Monitoring

After each phase, monitor:

1. **Application logs**: Check for errors related to insights
2. **API logs**: Verify insights endpoints are being called
3. **Milvus**: Ensure data is being written correctly
4. **Performance**: Check response times for insight operations

## Timeline

- **Phase 1**: ✅ Completed (2025-12-16)
- **Phase 2**: 1-2 days (update busibox-app)
- **Phase 3**: 1 week (update and deploy all apps)
- **Phase 4**: After 1 month of stable operation

## Success Criteria

- ✅ All insights functionality works in agent-api
- ⏳ busibox-app supports configurable insights URL
- ⏳ All consuming apps updated and deployed
- ⏳ No usage of deprecated search-api endpoints
- ⏳ search-api insights code removed

## Related Documentation

- [insights-migration-to-agent-api.md](./insights-migration-to-agent-api.md) - Original plan
- [insights-migration-completed.md](./insights-migration-completed.md) - Phase 1 completion
- `openapi/agent-api.yaml` - Agent API specification
- `srv/agent/app/api/insights.py` - Insights routes implementation
