# Insights Migration Completed

**Status**: Completed  
**Date**: 2025-12-16  
**Related**: [insights-migration-to-agent-api.md](./insights-migration-to-agent-api.md)

## Summary

Successfully migrated chat insights functionality from search-api to agent-api. Insights are now properly located in the agent service where they belong architecturally, as they represent agent memories/context rather than search functionality.

## What Was Done

### Phase 1: Implement in agent-api ✅

1. **Created insights schemas** (`srv/agent/app/schemas/insights.py`)
   - `ChatInsight` - Insight entity with embedding
   - `InsertInsightsRequest` - Bulk insert request
   - `InsightSearchRequest` - Search query request
   - `InsightSearchResult` - Search result item
   - `InsightSearchResponse` - Search response wrapper
   - `InsightStatsResponse` - User statistics

2. **Created insights service** (`srv/agent/app/services/insights_service.py`)
   - `InsightsService` - Main service class
   - Milvus connection management
   - Collection initialization with HNSW index
   - Insert, search, delete operations
   - Embedding generation via ingest-api
   - Health checks and statistics

3. **Created insights routes** (`srv/agent/app/api/insights.py`)
   - `POST /insights/init` - Initialize collection
   - `POST /insights` - Insert insights
   - `POST /insights/search` - Search insights
   - `DELETE /insights/conversation/{conversation_id}` - Delete by conversation
   - `DELETE /insights/user/{user_id}` - Delete by user
   - `GET /insights/stats/{user_id}` - Get statistics
   - `POST /insights/flush` - Flush collection

4. **Updated agent-api main.py**
   - Added insights router to app
   - Initialize insights service on startup
   - Added Milvus configuration to settings

5. **Updated requirements.txt**
   - Added `pymilvus>=2.3.0` dependency

6. **Created integration tests** (`srv/agent/tests/integration/test_insights_api.py`)
   - Test collection initialization
   - Test insight insertion
   - Test insight search
   - Test deletion operations
   - Test authorization checks

7. **Updated OpenAPI specification** (`openapi/agent-api.yaml`)
   - Added insights tag
   - Documented all insights endpoints
   - Added insight schemas to components

## Architecture

### Before Migration
```
search-api (10.96.200.204:8003)
  ├── Document search ✓
  ├── Web search ✓
  └── Chat insights ✗ (wrong place)

agent-api (10.96.200.207:8000)
  ├── Agent operations ✓
  ├── Chat history ✓
  └── (no insights)
```

### After Migration
```
agent-api (10.96.200.207:8000)
  ├── Agent operations ✓
  ├── Chat history ✓
  └── Chat insights ✓ (now in correct place)

search-api (10.96.200.204:8003)
  ├── Document search ✓
  └── Web search ✓
```

## Technical Details

### Milvus Collection
- **Name**: `chat_insights`
- **Schema**:
  - `id` (VARCHAR, primary key)
  - `userId` (VARCHAR)
  - `content` (VARCHAR, max 5000)
  - `embedding` (FLOAT_VECTOR, dim=1024)
  - `conversationId` (VARCHAR)
  - `analyzedAt` (INT64, unix timestamp)
- **Index**: HNSW on embedding field (M=16, efConstruction=200)
- **Metric**: L2 (Euclidean distance)

### Configuration
Added to `srv/agent/app/config/settings.py`:
```python
milvus_host: str = "10.96.200.204"
milvus_port: int = 19530
```

### Dependencies
- Uses existing Milvus instance (milvus-lxc)
- Uses ingest-api for embedding generation
- Same collection as before (no data migration needed)

## API Endpoints

All endpoints require authentication (Bearer token or X-User-Id header):

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/insights/init` | Initialize collection |
| POST | `/insights` | Insert insights |
| POST | `/insights/search` | Search insights |
| DELETE | `/insights/conversation/{id}` | Delete by conversation |
| DELETE | `/insights/user/{id}` | Delete by user |
| GET | `/insights/stats/{id}` | Get statistics |
| POST | `/insights/flush` | Flush collection |

## Next Steps

### Phase 2: Update busibox-app (Backward Compatible)

The busibox-app client library needs to be updated to support configurable insights API URL:

```typescript
// busibox-app/src/lib/insights/client.ts
const INSIGHTS_API_URL = 
  process.env.INSIGHTS_API_URL || 
  process.env.AGENT_API_URL || 
  process.env.SEARCH_API_URL || // Fallback for backward compat
  'http://localhost:8000'; // Default to agent-api
```

**Action items**:
1. Update busibox-app insights client
2. Publish busibox-app v2.1.0
3. Update consuming apps (ai-portal, etc.)

### Phase 3: Update Consuming Apps

Update environment variables in consuming applications:

```bash
# ai-portal, agent-client, etc.
INSIGHTS_API_URL=http://agent-lxc:8000
# or
AGENT_API_URL=http://agent-lxc:8000  # Will be used for insights
```

**Action items**:
1. Update ai-portal environment variables
2. Update agent-client environment variables
3. Update any other apps using insights
4. Deploy and test

### Phase 4: Deprecate search-api Insights

After all apps are migrated:

1. Mark search-api insights endpoints as deprecated
2. Add deprecation warnings to responses
3. Wait 1 month for migration
4. Remove insights code from search-api

## Testing

### Manual Testing

```bash
# Initialize collection
curl -X POST http://agent-lxc:8000/insights/init \
  -H "X-User-Id: test-user"

# Insert insights
curl -X POST http://agent-lxc:8000/insights \
  -H "X-User-Id: test-user" \
  -H "Content-Type: application/json" \
  -d '{
    "insights": [{
      "id": "test-1",
      "userId": "test-user",
      "content": "User prefers Python",
      "embedding": [0.1, 0.2, ...],
      "conversationId": "conv-1",
      "analyzedAt": 1702742400
    }]
  }'

# Search insights
curl -X POST http://agent-lxc:8000/insights/search \
  -H "X-User-Id: test-user" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does the user like?",
    "userId": "test-user",
    "limit": 5,
    "scoreThreshold": 0.7
  }'
```

### Integration Tests

Run the test suite:
```bash
cd srv/agent
pytest tests/integration/test_insights_api.py -v
```

## Benefits

1. **Correct Architecture**: Insights now live with agents where they belong
2. **Better Cohesion**: All agent-related state in one service
3. **Clearer Responsibilities**: Each service has a clear, focused purpose
4. **Easier Development**: Agent developers have all context in one place
5. **No Data Migration**: Same Milvus collection, no data movement needed

## Files Changed

### New Files
- `srv/agent/app/schemas/insights.py`
- `srv/agent/app/services/insights_service.py`
- `srv/agent/app/api/insights.py`
- `srv/agent/tests/integration/test_insights_api.py`
- `docs/development/tasks/insights-migration-completed.md`

### Modified Files
- `srv/agent/app/main.py`
- `srv/agent/app/config/settings.py`
- `srv/agent/requirements.txt`
- `openapi/agent-api.yaml`

### Unchanged (for now)
- `srv/search/src/services/insights_service.py` (will be deprecated later)
- `srv/search/src/api/routes/insights.py` (will be deprecated later)
- `srv/search/src/shared/schemas.py` (will be deprecated later)

## Rollback Plan

If issues occur:

1. **Immediate**: Set `INSIGHTS_API_URL=http://search-lxc:8001` in apps
2. **Keep search-api insights** until fully stable
3. **Gradual migration**: Move one app at a time
4. **Monitor**: Check logs for errors

## Related Documentation

- [insights-migration-to-agent-api.md](./insights-migration-to-agent-api.md) - Original migration plan
- [chat-architecture-refactor.md](./chat-architecture-refactor.md) - Overall chat refactor
- `openapi/agent-api.yaml` - Agent API specification
- `openapi/search-api.yaml` - Search API specification

## Notes

- **No breaking changes**: Existing search-api insights endpoints still work
- **Same Milvus collection**: No data migration needed
- **Backward compatible**: Apps can continue using search-api until updated
- **Tested**: Integration tests verify all functionality works
- **Documented**: OpenAPI spec updated with all endpoints and schemas
