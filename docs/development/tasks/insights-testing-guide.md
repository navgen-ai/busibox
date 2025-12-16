# Insights API Testing Guide

**Status**: Ready for Testing  
**Date**: 2025-12-16  
**Related**: [insights-migration-completed.md](./insights-migration-completed.md)

## Overview

This guide covers testing the insights API endpoints in agent-api. Insights are agent memories/context extracted from conversations and stored in Milvus for RAG.

## Prerequisites

### Services Required

1. **agent-api** (port 8000) - The service we're testing
2. **Milvus** (port 19530) - Vector database for insights storage
3. **ingest-api** (port 8002) - Embedding generation service (for search tests)
4. **PostgreSQL** (port 5432) - Database for agent-api

### Check Services

```bash
# Check if agent-api is running
curl http://localhost:8000/health

# Check if Milvus is accessible (from agent-lxc)
# This will be checked by the insights service itself
```

## Testing Methods

### Method 1: Manual Testing with curl

Use the provided test script:

```bash
cd /path/to/busibox/srv/agent

# Test against local development server
AGENT_API_URL=http://localhost:8000 bash scripts/test-insights-manual.sh

# Test against deployed agent-lxc
AGENT_API_URL=http://agent-lxc:8000 bash scripts/test-insights-manual.sh

# Test against test environment
AGENT_API_URL=http://10.96.200.30:8000 bash scripts/test-insights-manual.sh
```

The script tests:
- ✅ Initialize collection
- ✅ Insert insights
- ✅ Flush collection
- ✅ Get user statistics
- ⚠️  Search insights (requires embedding service)
- ✅ Delete conversation insights
- ✅ Delete user insights

### Method 2: Integration Tests with pytest

```bash
cd /path/to/busibox/srv/agent

# Run just the insights tests
bash scripts/run-tests.sh integration
# or
pytest tests/integration/test_insights_api.py -v

# Run with coverage
pytest tests/integration/test_insights_api.py -v --cov=app/api/insights --cov=app/services/insights_service
```

### Method 3: Interactive Testing

Use curl commands directly:

#### 1. Initialize Collection

```bash
curl -X POST http://localhost:8000/insights/init \
  -H "X-User-Id: test-user"
```

Expected response:
```json
{
  "message": "Collection initialized successfully",
  "collection": "chat_insights"
}
```

#### 2. Insert Insights

```bash
curl -X POST http://localhost:8000/insights \
  -H "X-User-Id: test-user" \
  -H "Content-Type: application/json" \
  -d '{
    "insights": [{
      "id": "test-insight-1",
      "userId": "test-user",
      "content": "User prefers Python for backend development",
      "embedding": [0.1, 0.2, ...1024 values...],
      "conversationId": "conv-123",
      "analyzedAt": 1702742400
    }]
  }'
```

Expected response:
```json
{
  "message": "Successfully inserted 1 insights",
  "count": 1
}
```

#### 3. Flush Collection

```bash
curl -X POST http://localhost:8000/insights/flush \
  -H "X-User-Id: test-user"
```

Expected response:
```json
{
  "message": "Collection flushed successfully",
  "collection": "chat_insights"
}
```

#### 4. Search Insights

```bash
curl -X POST http://localhost:8000/insights/search \
  -H "X-User-Id: test-user" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does the user like?",
    "userId": "test-user",
    "limit": 5,
    "scoreThreshold": 0.7
  }'
```

Expected response:
```json
{
  "query": "What does the user like?",
  "results": [
    {
      "id": "test-insight-1",
      "userId": "test-user",
      "content": "User prefers Python for backend development",
      "conversationId": "conv-123",
      "analyzedAt": "2023-12-16T12:00:00",
      "score": 0.45
    }
  ],
  "count": 1
}
```

#### 5. Get User Stats

```bash
curl http://localhost:8000/insights/stats/test-user \
  -H "X-User-Id: test-user"
```

Expected response:
```json
{
  "userId": "test-user",
  "count": 5,
  "collectionName": "chat_insights"
}
```

#### 6. Delete Conversation Insights

```bash
curl -X DELETE http://localhost:8000/insights/conversation/conv-123 \
  -H "X-User-Id: test-user"
```

Expected response:
```json
{
  "message": "Deleted insights for conversation conv-123",
  "conversationId": "conv-123"
}
```

#### 7. Delete User Insights

```bash
curl -X DELETE http://localhost:8000/insights/user/test-user \
  -H "X-User-Id: test-user"
```

Expected response:
```json
{
  "message": "Deleted all insights for user test-user",
  "userId": "test-user"
}
```

## Testing Checklist

### Basic Functionality
- [ ] Collection initialization works
- [ ] Can insert single insight
- [ ] Can insert multiple insights
- [ ] Can flush collection
- [ ] Can get user statistics
- [ ] Can delete conversation insights
- [ ] Can delete user insights

### Search Functionality (requires embedding service)
- [ ] Can search insights with query
- [ ] Results are filtered by user ID
- [ ] Score threshold works correctly
- [ ] Limit parameter works
- [ ] Returns relevant results

### Authentication & Authorization
- [ ] X-User-Id header authentication works
- [ ] Bearer token authentication works
- [ ] Users can only search their own insights
- [ ] Users can only delete their own insights
- [ ] Unauthorized requests return 401
- [ ] Cross-user access returns 403

### Error Handling
- [ ] Invalid JSON returns 422
- [ ] Missing required fields returns 422
- [ ] Invalid user ID returns appropriate error
- [ ] Milvus connection errors are handled
- [ ] Embedding service errors are handled

### Performance
- [ ] Bulk insert of 100 insights completes in < 5s
- [ ] Search query completes in < 2s
- [ ] Collection initialization is idempotent

## Common Issues

### Issue: "Insights service not initialized"

**Cause**: Insights service failed to initialize on startup

**Solution**:
```bash
# Check agent-api logs
journalctl -u agent-api -n 100

# Check Milvus connectivity
curl http://milvus-lxc:19530

# Restart agent-api
systemctl restart agent-api
```

### Issue: "No embeddings returned from service"

**Cause**: Embedding service (ingest-api) is not running or not accessible

**Solution**:
```bash
# Check ingest-api
curl http://ingest-lxc:8002/health

# Check network connectivity from agent-lxc
ssh root@agent-lxc
curl http://10.96.200.206:8002/health
```

### Issue: "Collection not found"

**Cause**: Collection hasn't been initialized

**Solution**:
```bash
# Initialize collection
curl -X POST http://agent-lxc:8000/insights/init \
  -H "X-User-Id: test-user"
```

### Issue: Search returns no results

**Possible causes**:
1. No insights inserted yet
2. Score threshold too low
3. User ID mismatch
4. Collection not flushed after insert

**Solution**:
```bash
# Check user stats
curl http://agent-lxc:8000/insights/stats/test-user \
  -H "X-User-Id: test-user"

# Flush collection
curl -X POST http://agent-lxc:8000/insights/flush \
  -H "X-User-Id: test-user"

# Try with higher score threshold
# (L2 distance: lower is better, so higher threshold = more results)
```

## Deployment Testing

### Test Environment

```bash
# Deploy agent-api to test environment
cd provision/ansible
make deploy-agent INV=inventory/test

# Run tests against test environment
AGENT_API_URL=http://10.96.200.30:8000 \
  bash srv/agent/scripts/test-insights-manual.sh
```

### Production Environment

```bash
# Deploy agent-api to production
cd provision/ansible
make deploy-agent

# Run smoke tests
AGENT_API_URL=http://agent-lxc:8000 \
  bash srv/agent/scripts/test-insights-manual.sh
```

## Integration with AI Portal

After agent-api is deployed and tested, update ai-portal:

```bash
# Update ai-portal environment
# .env or deployment config:
INSIGHTS_API_URL=http://agent-lxc:8000

# Deploy ai-portal
cd provision/ansible
make deploy-ai-portal

# Test insights in ai-portal
# 1. Create a conversation
# 2. Add messages
# 3. Check that insights are extracted
# 4. Verify insights appear in search
```

## Monitoring

### Logs

```bash
# Agent-api logs
journalctl -u agent-api -f | grep -i insight

# Check for errors
journalctl -u agent-api -n 1000 | grep -i "error.*insight"
```

### Metrics

Monitor:
- Insight insertion rate
- Search query latency
- Milvus connection health
- Embedding service availability

### Health Checks

```bash
# Agent-api health
curl http://agent-lxc:8000/health

# Check insights service is initialized
curl http://agent-lxc:8000/insights/stats/test-user \
  -H "X-User-Id: test-user"
```

## Success Criteria

- ✅ All manual tests pass
- ✅ Integration tests pass
- ✅ Can insert and search insights
- ✅ Authorization works correctly
- ✅ Performance meets requirements
- ✅ No errors in logs
- ✅ AI Portal integration works

## Next Steps

After testing is complete:
1. Update busibox-app to use agent-api for insights
2. Deploy to test environment
3. Run full integration tests
4. Deploy to production
5. Monitor for issues

## Related Documentation

- [insights-migration-completed.md](./insights-migration-completed.md) - Migration completion report
- [insights-migration-next-steps.md](./insights-migration-next-steps.md) - Next steps guide
- `openapi/agent-api.yaml` - API specification
- `srv/agent/app/api/insights.py` - Implementation
