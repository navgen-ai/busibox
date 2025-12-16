# Insights Migration - Deployment Checklist

**Status**: Ready for Deployment  
**Date**: 2025-12-16  
**Type**: Direct Cutover (no deprecation period)

## Pre-Deployment Checklist

### Code Changes Complete
- [x] Insights schemas created in agent-api
- [x] Insights service implemented in agent-api
- [x] Insights routes created in agent-api
- [x] Main.py updated with insights router
- [x] Requirements.txt updated with pymilvus
- [x] Integration tests created
- [x] OpenAPI spec updated
- [x] Documentation created

### Testing Complete
- [ ] Manual tests pass locally
- [ ] Integration tests pass
- [ ] Milvus connectivity verified
- [ ] Embedding service integration works
- [ ] Authorization checks work

### Busibox-app Updates
- [ ] Client updated with configurable URL
- [ ] Tests pass
- [ ] Version bumped to 2.1.0
- [ ] Published to npm

## Deployment Steps

### Phase 1: Deploy Agent-API (Test Environment)

```bash
cd /path/to/busibox/provision/ansible

# Deploy agent-api to test
make deploy-agent INV=inventory/test

# Verify deployment
ssh root@<test-agent-ip>
systemctl status agent-api
journalctl -u agent-api -n 50

# Test insights endpoints
AGENT_API_URL=http://<test-agent-ip>:8000 \
  bash ../../srv/agent/scripts/test-insights-manual.sh
```

**Verification**:
- [ ] Agent-API starts successfully
- [ ] Insights service initializes
- [ ] Milvus connection works
- [ ] All endpoints respond correctly

### Phase 2: Update AI Portal (Test Environment)

```bash
cd /path/to/ai-portal

# Update environment variables
# Edit .env or deployment config:
INSIGHTS_API_URL=http://<test-agent-ip>:8000

# Update dependencies
npm install @jazzmind/busibox-app@^2.1.0

# Build
npm run build

# Deploy
cd /path/to/busibox/provision/ansible
make deploy-ai-portal INV=inventory/test
```

**Verification**:
- [ ] AI Portal starts successfully
- [ ] Can create conversations
- [ ] Insights are extracted
- [ ] Insights can be searched
- [ ] No errors in logs

### Phase 3: Remove Insights from Search-API (Test Environment)

```bash
cd /path/to/busibox/srv/search

# Remove insights code
rm src/services/insights_service.py
rm src/api/routes/insights.py

# Update main.py - remove insights router
# Edit src/api/main.py and remove:
#   from api.routes import insights
#   app.include_router(insights.router, prefix="/insights", tags=["insights"])

# Update schemas.py - remove insight schemas
# Edit src/shared/schemas.py and remove:
#   - ChatInsight
#   - InsertInsightsRequest
#   - InsightSearchRequest
#   - InsightSearchResult
#   - InsightSearchResponse
#   - InsightStatsResponse

# Deploy
cd /path/to/busibox/provision/ansible
make deploy-search INV=inventory/test
```

**Verification**:
- [ ] Search-API starts successfully
- [ ] Document search still works
- [ ] Web search still works
- [ ] Insights endpoints return 404 (expected)

### Phase 4: Full Integration Test (Test Environment)

```bash
# Test complete flow
# 1. Create conversation in AI Portal
# 2. Add messages
# 3. Verify insights extracted
# 4. Search for insights
# 5. Delete conversation
# 6. Verify insights deleted
```

**Verification**:
- [ ] End-to-end flow works
- [ ] No errors in any service logs
- [ ] Performance is acceptable
- [ ] Data persists correctly

### Phase 5: Deploy to Production

**Only proceed if test environment is stable!**

```bash
cd /path/to/busibox/provision/ansible

# Deploy all services in order
# 1. Agent-API with insights
make deploy-agent

# 2. AI Portal with updated config
make deploy-ai-portal

# 3. Search-API without insights
make deploy-search

# 4. Any other apps using insights
# make deploy-<app>
```

**Verification**:
- [ ] All services start successfully
- [ ] No errors in logs
- [ ] Insights functionality works
- [ ] Search functionality works
- [ ] Monitor for 1 hour

## Rollback Plan

If issues occur during deployment:

### Immediate Rollback

```bash
# Option 1: Revert environment variable
# In ai-portal and other apps:
INSIGHTS_API_URL=http://search-lxc:8001

# Option 2: Restore search-api insights code
cd /path/to/busibox
git checkout HEAD~1 -- srv/search/src/services/insights_service.py
git checkout HEAD~1 -- srv/search/src/api/routes/insights.py
git checkout HEAD~1 -- srv/search/src/shared/schemas.py
git checkout HEAD~1 -- srv/search/src/api/main.py

# Redeploy search-api
cd provision/ansible
make deploy-search
```

### Partial Rollback

If only one app has issues:
```bash
# Rollback just that app
INSIGHTS_API_URL=http://search-lxc:8001 make deploy-<app>
```

## Post-Deployment Monitoring

### First Hour
Monitor every 5 minutes:
- [ ] Agent-API logs for errors
- [ ] AI Portal logs for errors
- [ ] Milvus logs for errors
- [ ] Insight insertion rate
- [ ] Search query success rate

### First Day
Monitor every hour:
- [ ] Service health
- [ ] Error rates
- [ ] Performance metrics
- [ ] User reports

### First Week
Monitor daily:
- [ ] Overall system health
- [ ] Insight data growth
- [ ] Query performance
- [ ] Any anomalies

## Success Criteria

### Technical
- ✅ All services running without errors
- ✅ Insights being inserted successfully
- ✅ Search queries returning results
- ✅ Performance within acceptable limits
- ✅ No data loss

### Functional
- ✅ Users can create conversations
- ✅ Insights are extracted automatically
- ✅ Insights appear in search results
- ✅ Conversation deletion removes insights
- ✅ User experience unchanged

## Communication Plan

### Before Deployment
- [ ] Notify team of deployment window
- [ ] Share rollback plan
- [ ] Assign monitoring responsibilities

### During Deployment
- [ ] Update team on progress
- [ ] Report any issues immediately
- [ ] Coordinate service restarts

### After Deployment
- [ ] Confirm successful deployment
- [ ] Share monitoring results
- [ ] Document any issues encountered

## Deployment Log

### Test Environment
- **Date**: ___________
- **Deployed by**: ___________
- **Result**: ___________
- **Issues**: ___________
- **Notes**: ___________

### Production Environment
- **Date**: ___________
- **Deployed by**: ___________
- **Result**: ___________
- **Issues**: ___________
- **Notes**: ___________

## Related Documentation

- [insights-migration-to-agent-api.md](./insights-migration-to-agent-api.md) - Migration plan
- [insights-migration-completed.md](./insights-migration-completed.md) - Implementation details
- [insights-testing-guide.md](./insights-testing-guide.md) - Testing procedures
- [insights-migration-next-steps.md](./insights-migration-next-steps.md) - Next steps (deprecated - using direct cutover)

## Notes

- This is a **direct cutover** deployment - no deprecation period
- All services must be deployed in the same maintenance window
- Test environment deployment is mandatory before production
- Monitor closely for the first 24 hours after production deployment
