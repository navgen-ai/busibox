# Testing Guide

**Created**: 2025-12-09  
**Last Updated**: 2025-12-15  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `guides/02-deployment.md`  
- `guides/testing/master-guide.md`  
- `guides/testing/TEST_STRATEGY.md`  
- `guides/agent-server-testing.md`

## Platform Checks (Ansible)
- **Smoke/health**:
  ```bash
  cd provision/ansible
  make verify          # health checks across services
  make verify-health   # service health subset
  make verify-smoke    # DB and container basics
  ```

## Service Testing

### Ingestion Service
- Unit/integration (inside repo or container):
  ```bash
  cd srv/ingest
  pip install -r requirements.txt
  pytest tests/api     # API routes incl. upload/status/search
  pytest tests/integration -m "not slow"  # pipeline coverage
  ```
- End-to-end upload + SSE + indexing:
  - Use `tests/integration/test_full_pipeline.py` (requires MinIO, Redis, Milvus running).

### Search Service
- Run search tests:
  ```bash
  cd srv/search
  pip install -r requirements.txt
  pytest tests/integration/test_search_api.py
  ```
- Ensure Milvus is seeded with ingest-produced partitions before running.

### Agent Server
- **Quick start**:
  ```bash
  cd srv/agent
  source venv/bin/activate
  make test              # All tests
  make test-unit         # Fast unit tests only
  make test-integration  # Integration tests with DB
  make test-cov          # With coverage report
  ```

- **Deployed testing** (via MCP):
  ```bash
  cd provision/ansible
  make test-agent INV=inventory/test
  make test-agent-coverage INV=inventory/test
  ```

- **Test structure**:
  - Unit tests: Auth, tokens, agents, workflows, scoring (117 tests)
  - Integration tests: API endpoints, SSE streaming, RBAC (40+ tests)
  - See `guides/agent-server-testing.md` for complete details

### AuthZ Service
- **Integration tests**:
  ```bash
  cd srv/authz
  source venv/bin/activate
  pytest tests/ -v
  ```
- **Test coverage**:
  - OAuth2 token exchange
  - RBAC management
  - Admin endpoints
  - Client credentials flow

## Bootstrap Test Credentials

For local integration testing with real services:

```bash
cd provision/ansible
make bootstrap-test-creds INV=inventory/test
```

This generates:
- Test OAuth client credentials
- Admin token for RBAC operations
- Test user with roles
- Ready-to-copy `.env` variables

See `guides/bootstrap-test-credentials.md` for details.

## Apps
- App-specific tests live in their repos (AI Portal, Agent Client). Run `npm test`/`npm run test:watch` per repo after wiring env vars to container endpoints.

## What to Verify After Deployment
- Ingest `/health` and Search `/health` return 200.
- Upload via ingest returns `queued` or `completed` for images/videos; status stream reaches `completed`.
- Search returns results limited to the user's partitions (use different JWTs to confirm access control).
- AuthZ token issuance works and audit rows are written.
- Agent server responds to `/health` and can execute runs.

## Related Documentation
- **Detailed guides**: `guides/agent-server-testing.md`, `guides/bootstrap-test-credentials.md`
- **Test strategy**: `guides/testing/TEST_STRATEGY.md`
- **Service testing**: `guides/testing/master-guide.md`
